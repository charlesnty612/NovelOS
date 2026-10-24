// =============================================================================
// 环节分配面板测试——环节级思考档覆盖（强度下拉 / 徽标 / PUT 语义）
// -----------------------------------------------------------------------------
// 对应契约 E（文件头注释）：① 有覆盖 ⇒ 下拉回显 + 徽标显示档位；② null ⇒ 灰字
// 「跟随档案」；③ 选档位 ⇒ PUT body thinking_override=<值>；④ 选「跟随档案」⇒
// PUT body thinking_override=null（显式清除，不是缺键）；⑤ 未绑定环节下拉禁用；
// ⑥ 端点收窄：绑定连接的档案 base_url 归连接（resolveProfileEndpoint）。
// =============================================================================

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CapabilityBindingsPanel, resolveProfileEndpoint } from './AiSettingsCapabilityBindings';
import { capabilityBindingsApi, connectionsApi, modelProfilesApi } from '../api/endpoints';
import type {
  CapabilityBinding,
  ModelConnectionOut,
  ModelProfile,
} from '../api/types';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    modelProfilesApi: {
      list: vi.fn(),
    },
    capabilityBindingsApi: {
      list: vi.fn(),
      bind: vi.fn(),
      unbind: vi.fn(),
    },
    connectionsApi: {
      list: vi.fn(),
    },
  };
});

const profileCustom: ModelProfile = {
  profile_id: 'mpf_custom',
  name: 'custom-openai',
  provider: 'openai',
  model: 'gpt-4o-mini',
  params: { base_url: 'https://api.openai.com/v1', api_key: '***' },
  enabled: 1,
};

const connFixture: ModelConnectionOut = {
  connection_id: 'mcn_001',
  name: 'opencode',
  provider: 'openai_compatible',
  base_url: 'https://opencode.example.com/v1',
  params_json: { api_key: '***' },
  enabled: 1,
  created_at: '2026-10-24T10:00:00+00:00',
  updated_at: '2026-10-24T10:00:00+00:00',
  has_api_key: true,
};

/** 绑定连接的档案：地址归连接，params 无 base_url。 */
const profileBound: ModelProfile = {
  profile_id: 'mpf_bound',
  name: 'bound-openai-compatible',
  provider: 'openai_compatible',
  model: 'glm-4',
  params: {},
  enabled: 1,
  connection_id: 'mcn_001',
};

function binding(
  capability: string,
  overrides: Partial<CapabilityBinding> = {},
): CapabilityBinding {
  return {
    capability,
    label: capability,
    agents: ['agent_x'],
    profile_ids: ['mpf_custom'],
    profiles: [{ profile_id: 'mpf_custom', name: 'custom-openai', model: 'gpt-4o-mini' }],
    legacy_available: false,
    thinking_override: null,
    ...overrides,
  };
}

beforeEach(() => {
  // 清调用计数（否则上个用例的 bind 调用会累计进下个用例的 toHaveBeenCalledTimes）
  vi.clearAllMocks();
  vi.mocked(modelProfilesApi.list).mockResolvedValue([profileCustom, profileBound]);
  vi.mocked(connectionsApi.list).mockResolvedValue([connFixture]);
  vi.mocked(capabilityBindingsApi.bind).mockImplementation(
    async (capability, profileIds, thinkingOverride) =>
      binding(capability, { profile_ids: profileIds, thinking_override: thinkingOverride ?? null }),
  );
});

describe('CapabilityBindingsPanel 强度覆盖', () => {
  it('thinking_override=low ⇒ 下拉回显 low，徽标显示「强度·低」', async () => {
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      binding('reasoning', { label: '推理规划', thinking_override: 'low' }),
    ]);
    render(<CapabilityBindingsPanel />);
    const select = (await screen.findByTestId(
      'cap-binding-thinking-select-reasoning',
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('low'));
    const badge = screen.getByTestId('cap-binding-thinking-badge-reasoning');
    expect(badge.textContent).toContain('强度·低');
    expect(badge.textContent).not.toContain('跟随档案');
  });

  it('thinking_override=null ⇒ 徽标显示灰字「跟随档案」', async () => {
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      binding('reasoning', { label: '推理规划', thinking_override: null }),
    ]);
    render(<CapabilityBindingsPanel />);
    const select = (await screen.findByTestId(
      'cap-binding-thinking-select-reasoning',
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe(''));
    expect(screen.getByTestId('cap-binding-thinking-badge-reasoning').textContent).toBe(
      '跟随档案',
    );
  });

  it('选「开启·低档」⇒ PUT body thinking_override=low（且不改候选链）', async () => {
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      binding('reasoning', { label: '推理规划', thinking_override: null }),
    ]);
    render(<CapabilityBindingsPanel />);
    const select = await screen.findByTestId('cap-binding-thinking-select-reasoning');
    fireEvent.change(select, { target: { value: 'low' } });
    await waitFor(() =>
      expect(capabilityBindingsApi.bind).toHaveBeenCalledTimes(1),
    );
    expect(vi.mocked(capabilityBindingsApi.bind).mock.calls[0]).toEqual([
      'reasoning',
      ['mpf_custom'],
      'low',
    ]);
  });

  it('选「跟随档案」⇒ PUT body thinking_override=null（显式 null，不是缺键）', async () => {
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      binding('reasoning', { label: '推理规划', thinking_override: 'high' }),
    ]);
    render(<CapabilityBindingsPanel />);
    const select = await screen.findByTestId('cap-binding-thinking-select-reasoning');
    fireEvent.change(select, { target: { value: '' } });
    await waitFor(() =>
      expect(capabilityBindingsApi.bind).toHaveBeenCalledTimes(1),
    );
    expect(vi.mocked(capabilityBindingsApi.bind).mock.calls[0]).toEqual([
      'reasoning',
      ['mpf_custom'],
      null,
    ]);
  });

  it('未绑定环节（链空）⇒ 强度下拉禁用', async () => {
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      binding('light', { label: '轻量评审', profile_ids: [], profiles: [] }),
    ]);
    render(<CapabilityBindingsPanel />);
    const select = (await screen.findByTestId(
      'cap-binding-thinking-select-light',
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.disabled).toBe(true));
  });

  it('强度选项按首选档案端点收窄（openai 官方 ⇒ 不含 on/adaptive/max）', async () => {
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      binding('reasoning', { label: '推理规划' }),
    ]);
    render(<CapabilityBindingsPanel />);
    const select = (await screen.findByTestId(
      'cap-binding-thinking-select-reasoning',
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBeGreaterThan(0));
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toContain(''); // 「跟随档案」恒在
    expect(values).toContain('low');
    expect(values).not.toContain('on');
    expect(values).not.toContain('adaptive');
    expect(values).not.toContain('max');
  });

  it('提示文案包含「环节强度覆盖档案的思考档」', async () => {
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue([
      binding('reasoning', { label: '推理规划' }),
    ]);
    render(<CapabilityBindingsPanel />);
    expect(
      await screen.findByText(/环节强度覆盖档案的思考档/),
    ).toBeTruthy();
  });
});

describe('resolveProfileEndpoint（强度收窄的端点解析）', () => {
  it('自定义态档案：base_url 取 params.base_url，provider 取档案自身', () => {
    expect(resolveProfileEndpoint(profileCustom, [connFixture])).toEqual({
      provider: 'openai',
      baseUrl: 'https://api.openai.com/v1',
    });
  });

  it('绑定连接的档案：params 无 base_url ⇒ 回退连接 base_url', () => {
    expect(resolveProfileEndpoint(profileBound, [connFixture])).toEqual({
      provider: 'openai_compatible',
      baseUrl: 'https://opencode.example.com/v1',
    });
  });

  it('档案缺失 ⇒ 空串（getThinkingOptions 对未知端点退回全量）', () => {
    expect(resolveProfileEndpoint(undefined, [connFixture])).toEqual({
      provider: '',
      baseUrl: '',
    });
  });
});
