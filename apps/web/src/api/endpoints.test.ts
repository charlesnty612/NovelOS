import { describe, expect, it } from 'vitest';
import { normalizeProfile } from './endpoints';
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
