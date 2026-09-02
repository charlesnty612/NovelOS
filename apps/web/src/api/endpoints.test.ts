import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { normalizeProfile, referenceApi } from './endpoints';
import { ApiError } from './client';
import type { ModelProfile } from './types';

// 回归：后端 _mask_response 读路径返回的键是 params_json（dict），
// 前端表单统一读 params。曾因读错键把 params 映射成空对象，导致
// 编辑弹窗丢 base_url/思考参数（连带「拉取模型」按钮置灰、保存丢参）。
describe('normalizeProfile', () => {
  const baseRow = {
    profile_id: 'mprof_test',
    name: '测试档案',
    provider: 'openai_compatible',
    model: 'test-model',
    enabled: 1,
    has_api_key: true,
  } as unknown as ModelProfile;

  it('后端 params_json（dict）→ 前端 params', () => {
    const row = {
      ...baseRow,
      params_json: {
        base_url: 'https://api.minimaxi.com/v1',
        thinking: { type: 'adaptive' },
        api_key: '***',
      },
    } as unknown as ModelProfile;
    const out = normalizeProfile(row);
    expect(out.params['base_url']).toBe('https://api.minimaxi.com/v1');
    expect(out.params['thinking']).toEqual({ type: 'adaptive' });
    expect(out.has_api_key).toBe(true);
  });

  it('后端 params_json（JSON 字符串）也能解析', () => {
    const row = {
      ...baseRow,
      params_json: '{"base_url":"https://x/v1"}',
    } as unknown as ModelProfile;
    const out = normalizeProfile(row);
    expect(out.params['base_url']).toBe('https://x/v1');
  });

  it('夹具直给 params（旧形态）时保持兼容', () => {
    const row = {
      ...baseRow,
      params: { base_url: 'https://y/v1' },
    } as unknown as ModelProfile;
    const out = normalizeProfile(row);
    expect(out.params['base_url']).toBe('https://y/v1');
  });

  it('两者都缺失时 params 兜底为空对象', () => {
    const out = normalizeProfile({ ...baseRow });
    expect(out.params).toEqual({});
  });
});

// referenceApi.uploadDeconstruct 测试：
// - F9 修复：网络层 reject 时包装成 ``new ApiError(0, msg)``，与 client.ts 一致；
//   **不**裸抛 TypeError，避免 UI catch 路径需要额外分支。
// - 顺手覆盖非 2xx → ApiError 路径。
describe('referenceApi.uploadDeconstruct (网络层失败)', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('F9 修复：fetch reject → 包装成 ApiError(0, msg)，不裸抛 TypeError', async () => {
    // 模拟断网 / CORS / 服务端进程死亡：fetch reject 一个 TypeError。
    globalThis.fetch = vi
      .fn()
      .mockRejectedValue(new TypeError('Failed to fetch')) as unknown as typeof fetch;

    const file = new File(['第一章 内容'], 'ref-book.txt', { type: 'text/plain' });
    let caught: unknown;
    try {
      await referenceApi.uploadDeconstruct('p1', file, {
        book_title: 'X',
        reader_profile: 'male_fantasy',
      });
      throw new Error('should have thrown');
    } catch (e: unknown) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    const ae = caught as ApiError;
    expect(ae.status).toBe(0);
    expect(ae.detail).toContain('Failed to fetch');
    // 关键：不是 TypeError（裸抛被禁止）
    expect(ae).not.toBeInstanceOf(TypeError);
  });

  it('非 2xx 抛 ApiError 并把 response.detail 透出', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: '无法解码，请确认 txt 为 utf-8/gb18030/utf-16' }), {
        status: 400,
        headers: { 'content-type': 'application/json' },
      }),
    ) as unknown as typeof fetch;

    const file = new File(['random binary'], 'ref.bin', { type: 'application/octet-stream' });
    let caught: unknown;
    try {
      await referenceApi.uploadDeconstruct('p1', file);
      throw new Error('should have thrown');
    } catch (e: unknown) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    const ae = caught as ApiError;
    expect(ae.status).toBe(400);
    expect(ae.detail).toContain('utf-8');
  });
});