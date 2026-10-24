import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AiSettingsPage, inferThinkingMode } from './AiSettingsPage';
import {
  agentsApi,
  capabilityBindingsApi,
  connectionsApi,
  modelProfilesApi,
} from '../api/endpoints';
import type {
  Agent,
  CapabilityBinding,
  ModelConnectionOut,
  ModelProfile,
} from '../api/types';
import { ApiError } from '../api/client';

vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    agentsApi: {
      list: vi.fn(),
      sync: vi.fn(),
      listPrompts: vi.fn(),
    },
    modelProfilesApi: {
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      test: vi.fn(),
      fetchAvailableModels: vi.fn(),
    },
    capabilityBindingsApi: {
      list: vi.fn(),
      bind: vi.fn(),
      unbind: vi.fn(),
    },
    // ⚠️ 必须显式覆盖：mock 工厂里的 `...actual` 会把真实实现带进来 ⇒ 发真请求。
    connectionsApi: {
      list: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      test: vi.fn(),
    },
  };
});

const baseProfile: ModelProfile = {
  profile_id: 'mpf_001',
  name: 'default-reasoning',
  provider: 'openai_compatible',
  model: 'gpt-4o-mini',
  params: {
    base_url: 'https://api.openai.com/v1',
    api_key: '***',  // 已脱敏
  },
  enabled: 1,
  has_api_key: true,
};

const profileNoKey: ModelProfile = {
  ...baseProfile,
  profile_id: 'mpf_002',
  name: 'plain',
  params: { base_url: 'https://api.openai.com/v1' },
  has_api_key: false,
};

/** 连接夹具（2026-10-21 批次十五「连接层」）。 */
const connFixture: ModelConnectionOut = {
  connection_id: 'mcn_001',
  name: 'opencode',
  provider: 'openai_compatible',
  base_url: 'https://opencode.ai/inference/go/v1',
  params_json: { api_key: '***' },
  enabled: 1,
  created_at: '2026-10-21T10:00:00+00:00',
  updated_at: '2026-10-21T10:00:00+00:00',
  has_api_key: true,
};

/** 挂在连接上的档案（地址 / 密钥归连接，档案自身没有 base_url）。 */
const boundProfile: ModelProfile = {
  profile_id: 'mpf_bound',
  name: 'opencode-creative',
  provider: 'openai_compatible',
  model: 'gpt-4o-mini',
  params: {},
  enabled: 1,
  has_api_key: false,
  connection_id: 'mcn_001',
};

const baseAgent: Agent = {
  agent_id: 'agt_director',
  name: 'director',
  role: 'planner',
  config_json: {},
  created_at: '2026-08-24T10:00:00+00:00',
  updated_at: '2026-08-24T10:00:00+00:00',
};

const bindingsFixture: CapabilityBinding[] = [
  {
    capability: 'premise_design',
    label: '题材定位',
    agents: ['premise_designer'],
    profile_ids: ['mpf_001'],
    profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
    legacy_available: false,
  },
  {
    capability: 'world_building',
    label: '世界观',
    agents: ['world_builder'],
    profile_ids: ['mpf_001'],
    profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
    legacy_available: false,
  },
  {
    capability: 'character_design',
    label: '角色设计',
    agents: ['character_designer'],
    profile_ids: ['mpf_001'],
    profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
    legacy_available: false,
  },
  {
    capability: 'volume_outline',
    label: '卷纲',
    agents: ['outline_designer'],
    profile_ids: ['mpf_001'],
    profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
    legacy_available: false,
  },
  {
    capability: 'creative_writing',
    label: '正文写作',
    agents: ['writer'],
    profile_ids: [],
    profiles: [],
    legacy_available: true,
  },
  {
    capability: 'reasoning',
    label: '推理规划',
    agents: ['director', 'arbiter'],
    profile_ids: ['mpf_001'],
    profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
    legacy_available: false,
  },
  {
    // V3.9.3：observer 拆为独立 capability，AI 设置页能单独分配模型。
    capability: 'observer',
    label: '状态提取',
    agents: ['observer'],
    profile_ids: ['mpf_001'],
    profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
    legacy_available: false,
  },
  {
    capability: 'light',
    label: '轻量评审',
    agents: ['critic'],
    profile_ids: [],
    profiles: [],
    legacy_available: false,
  },
];

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/projects/test-project/ai-settings']}>
      <AiSettingsPage />
    </MemoryRouter>,
  );
}

describe('AiSettingsPage · 模型档案 + 环节绑定', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(agentsApi.list).mockResolvedValue([baseAgent]);
    vi.mocked(agentsApi.listPrompts).mockResolvedValue([]);
    vi.mocked(agentsApi.sync).mockResolvedValue({
      scanned: [],
      registered: [],
      updated: [],
      agents: ['director'],
    });
    vi.mocked(modelProfilesApi.list).mockResolvedValue([baseProfile]);
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue(bindingsFixture);
    vi.mocked(connectionsApi.list).mockResolvedValue([connFixture]);
  });

  // -------------------- 模型档案：保留 create happy path + 掩码不回显 -----------------

  it('新建档案 happy path：必填 name/model 后提交，POST 体不带 capability 字段', async () => {
    vi.mocked(modelProfilesApi.create).mockResolvedValue({
      ...baseProfile,
      profile_id: 'mpf_new',
    });

    renderPage();

    fireEvent.click(await screen.findByTestId('new-model-profile'));

    fireEvent.change(screen.getByTestId('profile-name'), {
      target: { value: 'fast-creative' },
    });
    fireEvent.change(screen.getByTestId('profile-model'), {
      target: { value: 'gpt-4o-mini' },
    });
    fireEvent.change(screen.getByTestId('profile-base-url'), {
      target: { value: 'https://api.openai.com/v1' },
    });

    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(modelProfilesApi.create).mock
      .calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.name).toBe('fast-creative');
    expect(payload.model).toBe('gpt-4o-mini');
    // 档案与 capability 解耦：POST 体不再带 capability。
    expect(Object.prototype.hasOwnProperty.call(payload, 'capability')).toBe(false);
    const paramsOut = payload.params as Record<string, unknown>;
    expect(paramsOut).toHaveProperty('base_url', 'https://api.openai.com/v1');
    // 用户留空 api_key → 不传该字段（保留 DB 原值 / 新建时未配置）
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'api_key')).toBe(false);
  });

  it('编辑档案：api_key 已脱敏（***）不回填到输入框 value', async () => {
    renderPage();

    const editBtn = await screen.findByTestId('model-profile-edit-mpf_001');
    fireEvent.click(editBtn);

    const apiKeyInput = await screen.findByTestId('profile-api-key') as HTMLInputElement;
    expect(apiKeyInput.value).toBe(''); // 掩码交互沿用
    // 也没有任何密文字符串回显
    expect(apiKeyInput.value).not.toContain('***');
  });

  it('has_api_key=true 时显示「清除已存密钥」按钮', async () => {
    renderPage();

    const editBtn = await screen.findByTestId('model-profile-edit-mpf_001');
    fireEvent.click(editBtn);

    const clearBtn = await screen.findByTestId('profile-clear-api-key');
    expect(clearBtn).toBeEnabled();
    expect(clearBtn).toHaveTextContent('清除已存密钥');
  });

  it('点击「清除已存密钥」后保存 → PATCH body 含 api_key="" 且不含 capability', async () => {
    vi.mocked(modelProfilesApi.update).mockResolvedValue({
      ...baseProfile,
      has_api_key: false,
    });

    renderPage();

    const editBtn = await screen.findByTestId('model-profile-edit-mpf_001');
    fireEvent.click(editBtn);

    const clearBtn = await screen.findByTestId('profile-clear-api-key');
    fireEvent.click(clearBtn);
    await waitFor(() => {
      expect(clearBtn).toHaveTextContent('已请求清除');
    });

    fireEvent.click(screen.getByTestId('profile-save'));
    await waitFor(() => {
      expect(modelProfilesApi.update).toHaveBeenCalledTimes(1);
    });
    const [id, payload] = vi.mocked(modelProfilesApi.update).mock.calls[0]!;
    expect(id).toBe('mpf_001');
    expect(Object.prototype.hasOwnProperty.call(payload, 'capability')).toBe(false);
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    expect(paramsOut).toHaveProperty('api_key', '');
    expect(paramsOut).toHaveProperty('base_url', 'https://api.openai.com/v1');
  });

  it('has_api_key=false 时不显示「清除已存密钥」按钮', async () => {
    vi.mocked(modelProfilesApi.list).mockResolvedValue([profileNoKey]);

    renderPage();

    const editBtn = await screen.findByTestId('model-profile-edit-mpf_002');
    fireEvent.click(editBtn);
    await screen.findByTestId('profile-api-key');
    expect(screen.queryByTestId('profile-clear-api-key')).toBeNull();
  });

  // -------------------- 环节分配 ----------------------------------------------

  it('环节分配渲染 8 行（含中文 label + agents 小字）', async () => {
    renderPage();

    // 等列表到位
    await screen.findByTestId('capability-bindings-list');
    for (const b of bindingsFixture) {
      const row = screen.getByTestId(`cap-binding-${b.capability}`);
      expect(row).toBeInTheDocument();
      // label 中文
      expect(row).toHaveTextContent(b.label);
      // agents 小字（任意一个 agent 名出现即可）
      if (b.agents.length > 0) {
        expect(row).toHaveTextContent(b.agents[0]!);
      }
    }
    // 8 行
    const rows = document.querySelectorAll(
      '[data-testid^="cap-binding-"]:not([data-testid*="-select"]):not([data-testid*="-legacy"]):not([data-testid*="-saving"]):not([data-testid*="-saved"]):not([data-testid*="-error"]):not([data-testid*="-status"])',
    );
    // 过滤得到「cap-binding-{capability}」的行
    const fixed = bindingsFixture.filter((b) =>
      document.querySelector(`[data-testid="cap-binding-${b.capability}"]`),
    );
    expect(fixed).toHaveLength(8);
    // sanity: 至少查到 8 个匹配
    expect(rows.length).toBeGreaterThanOrEqual(8);
  });

  it('切换下拉发出 PUT 正确载荷（profile_ids 数组包裹单值）', async () => {
    vi.mocked(capabilityBindingsApi.bind).mockResolvedValue({
      capability: 'premise_design',
      label: '题材定位',
      agents: ['premise_designer'],
      profile_ids: ['mpf_001'],
      profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
      legacy_available: false,
    });

    renderPage();
    await screen.findByTestId('capability-bindings-list');

    const sel = await screen.findByTestId(
      'cap-binding-select-premise_design',
    ) as HTMLSelectElement;
    // 保持现有值不变也能触发 change
    fireEvent.change(sel, { target: { value: 'mpf_001' } });

    await waitFor(() => {
      expect(capabilityBindingsApi.bind).toHaveBeenCalledTimes(1);
    });
    const [capability, profileIds] = vi.mocked(capabilityBindingsApi.bind).mock.calls[0]!;
    expect(capability).toBe('premise_design');
    // bind(capability, profileIds) 第二个参数是数组；服务端 PUT body 由 api.put 包装为 {profile_ids: profileIds}
    expect(profileIds).toEqual(['mpf_001']);
  });

  it('未绑定时 legacy_available=true 出现「检测到旧版配置仍可用」提示', async () => {
    renderPage();
    await screen.findByTestId('capability-bindings-list');

    // creative_writing profile_ids=[] 且 legacy_available=true
    const sel = (await screen.findByTestId(
      'cap-binding-select-creative_writing',
    )) as HTMLSelectElement;
    expect(sel.value).toBe('');
    expect(
      screen.getByTestId('cap-binding-legacy-hint-creative_writing'),
    ).toHaveTextContent(/旧版配置/);
  });

  it('解绑（选空值）调用 DELETE 端点', async () => {
    vi.mocked(capabilityBindingsApi.unbind).mockResolvedValue(undefined as never);

    renderPage();
    await screen.findByTestId('capability-bindings-list');

    // premise_design 当前绑 mpf_001 → 切到空值触发 unbind
    const sel = (await screen.findByTestId(
      'cap-binding-select-premise_design',
    )) as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: '' } });

    await waitFor(() => {
      expect(capabilityBindingsApi.unbind).toHaveBeenCalledTimes(1);
    });
    expect(capabilityBindingsApi.unbind).toHaveBeenCalledWith('premise_design');
    // 不应发 bind
    expect(capabilityBindingsApi.bind).not.toHaveBeenCalled();
  });

  // -------------------- 档案卡「设为默认」-----------------------------

  it('档案卡展示其作为首选的环节徽标', async () => {
    // premise_design/world_building/character_design/volume_outline/reasoning 都把 mpf_001 放首位
    renderPage();
    const wrap = await screen.findByTestId('model-profile-default-caps-mpf_001');
    for (const b of bindingsFixture) {
      if (b.profile_ids[0] === 'mpf_001') {
        expect(
          screen.getByTestId(`model-profile-default-cap-badge-mpf_001-${b.capability}`),
        ).toHaveTextContent(b.label);
      } else {
        // profile_ids[0] 不是本档案的环节不出现在该徽标区
        expect(
          screen.queryByTestId(`model-profile-default-cap-badge-mpf_001-${b.capability}`),
        ).toBeNull();
      }
    }
    expect(wrap).toBeInTheDocument();
  });

  it('档案卡点击「设为默认」展开 8 环节 chip；再次点击收起', async () => {
    renderPage();

    const btn = await screen.findByTestId('profile-set-default-mpf_001');
    // 初始不展示 chip 行
    expect(screen.queryByTestId('profile-default-chip-row-mpf_001')).toBeNull();
    fireEvent.click(btn);
    const row = await screen.findByTestId('profile-default-chip-row-mpf_001');
    // 8 个 chip 都在
    for (const b of bindingsFixture) {
      expect(
        screen.getByTestId(`profile-default-cap-mpf_001-${b.capability}`),
      ).toBeInTheDocument();
    }
    expect(
      row.querySelectorAll('[data-testid^="profile-default-cap-mpf_001-"]').length,
    ).toBe(8);

    // 再次点击收起
    fireEvent.click(btn);
    await waitFor(() => {
      expect(screen.queryByTestId('profile-default-chip-row-mpf_001')).toBeNull();
    });
  });

  it('点击某环节 chip → bind(capability, [本档案, ...其余]) 首位 = 本档案 id', async () => {
    // creative_writing 当前 profile_ids=[]，绑本档案 → 期望 [mpf_001]
    // volume_outline 当前 profile_ids=['mpf_001']；绑一个不存在的 mpf_999（不参与 list）
    //   反而更稳：测「首位是新档案、其余保留」→ 选 mpf_001 作为新档案绑到 world_building（当前也是 mpf_001 首位）
    //   为了断言首位变化，选用 mpf_002（profileNoKey）作为新档案绑到 premise_design。
    // 但 mock 只列了 mpf_001；改用「仍能验证顺序正确」方式：
    //   - 把第一个档案再绑到 creative_writing（profile_ids=[]），结果应为 [mpf_001]。
    vi.mocked(modelProfilesApi.list).mockResolvedValue([baseProfile, profileNoKey]);
    vi.mocked(capabilityBindingsApi.bind).mockResolvedValue({
      capability: 'creative_writing',
      label: '正文写作',
      agents: ['writer'],
      profile_ids: ['mpf_002'],
      profiles: [
        { profile_id: 'mpf_002', name: 'plain', model: 'gpt-4o-mini' },
      ],
      legacy_available: false,
    });

    renderPage();
    const btn = await screen.findByTestId('profile-set-default-mpf_002');
    fireEvent.click(btn);
    const chip = await screen.findByTestId(
      'profile-default-cap-mpf_002-creative_writing',
    );
    fireEvent.click(chip);

    await waitFor(() => {
      expect(capabilityBindingsApi.bind).toHaveBeenCalledTimes(1);
    });
    const [capability, profileIds] = vi.mocked(capabilityBindingsApi.bind).mock.calls[0]!;
    expect(capability).toBe('creative_writing');
    // 首位必须是新档案
    expect(profileIds[0]).toBe('mpf_002');
    // 其余项不含本档案（无重复）
    expect(profileIds.filter((p) => p === 'mpf_002')).toHaveLength(1);
    // 整体数组等价于「本档案 + 现有列表中去掉本档案」
    expect(profileIds).toEqual(['mpf_002']);
  });

  it('已经是首选时点击 chip 幂等（不发起 bind）', async () => {
    // premise_design 首位已是 mpf_001；点 chip → 不该 bind
    renderPage();
    const btn = await screen.findByTestId('profile-set-default-mpf_001');
    fireEvent.click(btn);
    const chip = await screen.findByTestId(
      'profile-default-cap-mpf_001-premise_design',
    );
    fireEvent.click(chip);

    // 给一点时间确认没有副作用被触发
    await new Promise((r) => setTimeout(r, 10));
    expect(capabilityBindingsApi.bind).not.toHaveBeenCalled();
  });

  // -------------------- 思考模式 + 参数保留 ---------------------------------

  it('档案卡展示「思考：关/开/低/中/高/默认」徽标', async () => {
    // mpf_001 默认无思考配置 → 徽标应为「思考：默认」
    renderPage();
    const badge = await screen.findByTestId('profile-thinking-badge-mpf_001');
    expect(badge).toHaveTextContent('思考：默认');
  });

  it('编辑档案：思考模式下拉按 initialParams 回显 + 保存时 params 不丢已有键', async () => {
    // 编辑带 thinking={type:'disabled'}+timeout_s=42 的档案：
    // 打开表单时下拉应回显「关闭思考」；不改思考直接保存 → params 同时保留 timeout_s 与 thinking。
    const profileWithThinking: ModelProfile = {
      ...baseProfile,
      profile_id: 'mpf_t1',
      name: 'thinking-disabled',
      params: {
        base_url: 'https://api.openai.com/v1',
        api_key: '***',
        timeout_s: 42,
        thinking: { type: 'disabled' },
      },
    };
    vi.mocked(modelProfilesApi.list).mockResolvedValue([profileWithThinking]);
    vi.mocked(modelProfilesApi.update).mockResolvedValue(profileWithThinking);

    renderPage();

    const editBtn = await screen.findByTestId('model-profile-edit-mpf_t1');
    fireEvent.click(editBtn);

    // 下拉回显「关闭思考」
    const sel = (await screen.findByTestId(
      'profile-thinking-select',
    )) as HTMLSelectElement;
    expect(sel.value).toBe('off');

    // 档案卡徽标同步显示「思考：关」
    expect(
      screen.getByTestId('profile-thinking-badge-mpf_t1'),
    ).toHaveTextContent('思考：关');

    // 不改思考，直接保存
    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(modelProfilesApi.update).mock.calls[0]!;
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    // 参数不丢回归：timeout_s 原样保留
    expect(paramsOut).toHaveProperty('timeout_s', 42);
    // thinking 仍为 disabled
    expect(paramsOut).toHaveProperty('thinking');
    expect(
      (paramsOut['thinking'] as Record<string, unknown>)['type'],
    ).toBe('disabled');
    // 没有 reasoning_effort
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'reasoning_effort')).toBe(false);
  });

  it('编辑档案：切到「开启·中档」保存 → reasoning_effort=medium 且无 thinking 键', async () => {
    const profileWithThinking: ModelProfile = {
      ...baseProfile,
      profile_id: 'mpf_t2',
      name: 'thinking-on',
      params: {
        base_url: 'https://api.openai.com/v1',
        api_key: '***',
        thinking: { type: 'disabled' },
      },
    };
    vi.mocked(modelProfilesApi.list).mockResolvedValue([profileWithThinking]);
    vi.mocked(modelProfilesApi.update).mockResolvedValue(profileWithThinking);

    renderPage();
    fireEvent.click(await screen.findByTestId('model-profile-edit-mpf_t2'));

    const sel = await screen.findByTestId('profile-thinking-select');
    fireEvent.change(sel, { target: { value: 'medium' } });

    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(modelProfilesApi.update).mock.calls[0]!;
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    expect(paramsOut).toHaveProperty('reasoning_effort', 'medium');
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'thinking')).toBe(false);
  });

  it('编辑档案：切回「默认」保存 → params 含 reasoning_effort=null（显式删除）', async () => {
    const profileWithEffort: ModelProfile = {
      ...baseProfile,
      profile_id: 'mpf_t3',
      name: 'thinking-medium',
      params: {
        base_url: 'https://api.openai.com/v1',
        api_key: '***',
        reasoning_effort: 'medium',
      },
    };
    vi.mocked(modelProfilesApi.list).mockResolvedValue([profileWithEffort]);
    vi.mocked(modelProfilesApi.update).mockResolvedValue(profileWithEffort);

    renderPage();
    fireEvent.click(await screen.findByTestId('model-profile-edit-mpf_t3'));

    const sel = (await screen.findByTestId(
      'profile-thinking-select',
    )) as HTMLSelectElement;
    // initialParams 含 reasoning_effort='medium' → 应回显「开启·中档」
    expect(sel.value).toBe('medium');

    fireEvent.change(sel, { target: { value: 'default' } });
    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(modelProfilesApi.update).mock.calls[0]!;
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    // 与后端 null=删除语义对齐：原本存在 reasoning_effort → 发 null 显式删除
    expect(paramsOut).toHaveProperty('reasoning_effort', null);
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'thinking')).toBe(false);
  });

  it('编辑档案：原本无 reasoning_effort 时切「默认」保存 → payload 不含该键', async () => {
    // 初始 params 不含 reasoning_effort / thinking → 切「默认」保持缺键（非 null）
    const profileNoEffort: ModelProfile = {
      ...baseProfile,
      profile_id: 'mpf_t4',
      name: 'thinking-none',
      params: {
        base_url: 'https://api.openai.com/v1',
        api_key: '***',
      },
    };
    vi.mocked(modelProfilesApi.list).mockResolvedValue([profileNoEffort]);
    vi.mocked(modelProfilesApi.update).mockResolvedValue(profileNoEffort);

    renderPage();
    fireEvent.click(await screen.findByTestId('model-profile-edit-mpf_t4'));

    const sel = (await screen.findByTestId(
      'profile-thinking-select',
    )) as HTMLSelectElement;
    // initialParams 不含 reasoning_effort → 回显「默认」
    expect(sel.value).toBe('default');

    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(modelProfilesApi.update).mock.calls[0]!;
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'reasoning_effort')).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'thinking')).toBe(false);
  });

  // -------------------- 保存反馈 ------------------------------------------------

  it('新建档案成功：渲染「已保存」model-profiles-saved-banner', async () => {
    vi.mocked(modelProfilesApi.create).mockResolvedValue({
      ...baseProfile,
      profile_id: 'mpf_new',
      name: 'fast-creative',
    });

    renderPage();
    fireEvent.click(await screen.findByTestId('new-model-profile'));
    fireEvent.change(screen.getByTestId('profile-name'), {
      target: { value: 'fast-creative' },
    });
    fireEvent.change(screen.getByTestId('profile-model'), {
      target: { value: 'gpt-4o-mini' },
    });
    fireEvent.change(screen.getByTestId('profile-base-url'), {
      target: { value: 'https://api.openai.com/v1' },
    });
    fireEvent.click(screen.getByTestId('profile-save'));

    const banner = await screen.findByTestId('model-profiles-saved-banner');
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent(/已保存/);
  });

  // -------------------- V3.8 拉取模型 + 思考模式端点收窄 ----------------------------

  it('mock provider：拉取按钮始终可点，候选下拉出现 mock-model', async () => {
    // mock 不需要 base_url，按钮可点；内部直返 [mock-model]。
    vi.mocked(modelProfilesApi.fetchAvailableModels).mockResolvedValue({
      models: ['mock-model'],
    });

    renderPage();

    const newBtn = await screen.findByTestId('new-model-profile');
    fireEvent.click(newBtn);

    // 默认 provider 是 openai_compatible，需要切到 mock 才能看到「无需 base_url 即可拉取」路径
    const providerSel = (await screen.findByTestId(
      'profile-provider',
    )) as HTMLSelectElement;
    fireEvent.change(providerSel, { target: { value: 'mock' } });

    const fetchBtn = await screen.findByTestId('profile-fetch-models');
    expect(fetchBtn).toBeEnabled();

    fireEvent.click(fetchBtn);

    await waitFor(() => {
      expect(modelProfilesApi.fetchAvailableModels).toHaveBeenCalledTimes(1);
    });

    // 拉取成功后出现完整候选下拉（原生 datalist 会按输入值过滤候选，已弃用）。
    const cand = (await screen.findByTestId(
      'profile-model-candidates',
    )) as HTMLSelectElement;
    const opts = Array.from(cand.querySelectorAll('option')).map(
      (o) => (o as HTMLOptionElement).value,
    );
    expect(opts).toContain('mock-model');
  });

  it('MiniMax 域名：思考下拉只含 default / off / adaptive', async () => {
    const minimaxProfile: ModelProfile = {
      ...baseProfile,
      profile_id: 'mpf_mini',
      name: 'minimax-default',
      provider: 'openai_compatible',
      params: { base_url: 'https://api.minimax.chat/v1' },
    };
    vi.mocked(modelProfilesApi.list).mockResolvedValue([minimaxProfile]);

    renderPage();
    const editBtn = await screen.findByTestId('model-profile-edit-mpf_mini');
    fireEvent.click(editBtn);

    const sel = (await screen.findByTestId('profile-thinking-select')) as HTMLSelectElement;
    const values = Array.from(sel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(['default', 'off', 'adaptive']);
  });

  it('未知端点：思考下拉显示全量选项（含 max）', async () => {
    const unknownProfile: ModelProfile = {
      ...baseProfile,
      profile_id: 'mpf_unk',
      name: 'unknown-endpoint',
      provider: 'openai_compatible',
      params: { base_url: 'https://llm.example-private.com/v1' },
    };
    vi.mocked(modelProfilesApi.list).mockResolvedValue([unknownProfile]);

    renderPage();
    const editBtn = await screen.findByTestId('model-profile-edit-mpf_unk');
    fireEvent.click(editBtn);

    const sel = (await screen.findByTestId('profile-thinking-select')) as HTMLSelectElement;
    const values = Array.from(sel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value);
    // 全量 8 项含 max
    expect(values).toContain('max');
    expect(values).toContain('adaptive');
    expect(values).toContain('on');
    expect(values).toContain('low');
  });
});
// -------------------- 回归：思考档位回显 --------------------

describe('inferThinkingMode 回归（审查 2026-08-30）', () => {
  it('reasoning_effort=max 回显为 max（漏判会在重开编辑后静默丢档）', () => {
    expect(inferThinkingMode({ reasoning_effort: 'max' })).toBe('max');
    expect(inferThinkingMode({ reasoning_effort: 'low' })).toBe('low');
    expect(inferThinkingMode({ reasoning_effort: 'high' })).toBe('high');
  });

  it('thinking.type 各值回显正确', () => {
    expect(inferThinkingMode({ thinking: { type: 'adaptive' } })).toBe('adaptive');
    expect(inferThinkingMode({ thinking: { type: 'disabled' } })).toBe('off');
    expect(inferThinkingMode({ thinking: { type: 'enabled' } })).toBe('on');
    expect(inferThinkingMode({})).toBe('default');
  });
});

// -------------------- V3.10 整备回归：错误通道 / 请求去重 / 中文标签 --------------------

describe('AiSettingsPage · V3.10 整备回归', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(agentsApi.list).mockResolvedValue([baseAgent]);
    vi.mocked(agentsApi.listPrompts).mockResolvedValue([]);
    vi.mocked(modelProfilesApi.list).mockResolvedValue([baseProfile]);
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue(bindingsFixture);
    vi.mocked(connectionsApi.list).mockResolvedValue([connFixture]);
  });

  it('「编辑」直接用行数据打开表单，不再多发一次 list()', async () => {
    renderPage();

    const editBtn = await screen.findByTestId('model-profile-edit-mpf_001');
    const callsBefore = vi.mocked(modelProfilesApi.list).mock.calls.length;

    fireEvent.click(editBtn);
    // 表单打开（能拿到输入框）即说明行数据足够，无需重新拉列表
    await screen.findByTestId('profile-api-key');

    expect(vi.mocked(modelProfilesApi.list).mock.calls.length).toBe(callsBefore);
  });

  it('测试连接失败 → 错误通道（alert--error）；成功 → 信息通道（alert--info）', async () => {
    vi.mocked(modelProfilesApi.test).mockRejectedValueOnce(
      new Error('端点不可达'),
    );

    renderPage();
    fireEvent.click(await screen.findByTestId('model-profile-test-mpf_001'));

    const failed = await screen.findByTestId('model-profile-test-result');
    expect(failed.closest('.alert')?.className).toContain('alert--error');
    expect(failed).toHaveTextContent('端点不可达');

    vi.mocked(modelProfilesApi.test).mockResolvedValue({
      profile_id: 'mpf_001',
      ok: true,
      latency_ms: 12,
      detail: null,
      status_code: 200,
    });
    fireEvent.click(screen.getByTestId('model-profile-test-mpf_001'));

    await waitFor(() => {
      expect(
        screen.getByTestId('model-profile-test-result').closest('.alert')?.className,
      ).toContain('alert--info');
    });
  });

  it('档案状态徽标中文化：启用 / 停用（不再直出 enabled / disabled）', async () => {
    vi.mocked(modelProfilesApi.list).mockResolvedValue([
      baseProfile,
      { ...baseProfile, profile_id: 'mpf_off', name: 'off-profile', enabled: 0 },
    ]);

    renderPage();

    const onRow = await screen.findByTestId('model-profile-row-mpf_001');
    const offRow = await screen.findByTestId('model-profile-row-mpf_off');
    expect(onRow).toHaveTextContent('启用');
    expect(offRow).toHaveTextContent('停用');
    expect(onRow).not.toHaveTextContent('enabled');
    expect(offRow).not.toHaveTextContent('disabled');
  });
});

// =============================================================================
// 2026-10-21 批次十五「连接层」：档案表单双模（使用连接 / 自定义配置）
// =============================================================================

describe('AiSettingsPage · 模型连接（连接层）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(agentsApi.list).mockResolvedValue([baseAgent]);
    vi.mocked(agentsApi.listPrompts).mockResolvedValue([]);
    vi.mocked(modelProfilesApi.list).mockResolvedValue([baseProfile]);
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue(bindingsFixture);
    vi.mocked(connectionsApi.list).mockResolvedValue([connFixture]);
  });

  it('本页挂载了「模型连接」面板（判别点 = 面板自己的 data-testid）', async () => {
    renderPage();

    // ⚠️ 判别点必须是**被挂物专属**的：这里用面板自己的 testid，
    // 不用 modelProfilesApi.list 之类「父页面本来也会调的公共端点」。
    expect(await screen.findByTestId('model-connections-panel')).toBeInTheDocument();
    expect(screen.getByTestId('model-connections-panel')).toHaveTextContent('模型连接');
  });

  it('档案行显示所属连接名；未绑定的显示「独立配置」', async () => {
    vi.mocked(modelProfilesApi.list).mockResolvedValue([baseProfile, boundProfile]);

    renderPage();

    expect(
      await screen.findByTestId('profile-connection-badge-mpf_001'),
    ).toHaveTextContent('独立配置');
    expect(screen.getByTestId('profile-connection-badge-mpf_bound')).toHaveTextContent(
      'opencode',
    );
  });

  it('档案表单「使用连接」态：不显示地址 / 密钥输入框，提交带 connection_id 与连接的 provider', async () => {
    vi.mocked(modelProfilesApi.create).mockResolvedValue({
      ...boundProfile,
      profile_id: 'mpf_new',
    });

    renderPage();

    fireEvent.click(await screen.findByTestId('new-model-profile'));
    fireEvent.click(await screen.findByTestId('profile-mode-connection'));

    const sel = (await screen.findByTestId('profile-connection')) as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: 'mcn_001' } });

    // 连接态：地址 / 密钥输入框**不出现**
    expect(screen.queryByTestId('profile-base-url')).toBeNull();
    expect(screen.queryByTestId('profile-api-key')).toBeNull();

    fireEvent.change(screen.getByTestId('profile-name'), {
      target: { value: 'opencode-creative' },
    });
    fireEvent.change(screen.getByTestId('profile-model'), {
      target: { value: 'gpt-4o-mini' },
    });
    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(modelProfilesApi.create).mock
      .calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.connection_id).toBe('mcn_001');
    // provider 取自所选连接（连接是唯一权威）
    expect(payload.provider).toBe('openai_compatible');
    // 档案级不重复携带地址 / 密钥
    const paramsOut = payload.params as Record<string, unknown>;
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'base_url')).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'api_key')).toBe(false);
  });

  it('连接态「接口类型」可编辑：初值跟随所绑连接主协议，改成 anthropic 后提交体带所选协议（后端已放开一致性约束，mock 201）', async () => {
    // 2026-10-24 协议下放模型档案：同一厂商网关下不同模型走不同协议
    // （opencode 的 Claude/MiniMax/Qwen 系走 Anthropic，GLM/Kimi 走 OpenAI 兼容）。
    vi.mocked(modelProfilesApi.create).mockResolvedValue({
      ...boundProfile,
      profile_id: 'mpf_new',
      provider: 'anthropic',
    });

    renderPage();

    fireEvent.click(await screen.findByTestId('new-model-profile'));
    fireEvent.click(await screen.findByTestId('profile-mode-connection'));

    const sel = (await screen.findByTestId('profile-connection')) as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: 'mcn_001' } });

    // 接口类型是**可编辑下拉**（不再是只读跟随），初值 = 所绑连接的主协议
    const providerSel = (await screen.findByTestId(
      'profile-provider',
    )) as HTMLSelectElement;
    expect(providerSel.value).toBe('openai_compatible');
    // 模型级提示横幅在（InfoBanner 风格）
    expect(screen.getByTestId('profile-provider-hint')).toHaveTextContent('模型级');

    // 按模型实际支持的协议改掉 ⇒ 提交体带**所选**协议，而不是连接的主协议
    fireEvent.change(providerSel, { target: { value: 'anthropic' } });
    expect(providerSel.value).toBe('anthropic');

    fireEvent.change(screen.getByTestId('profile-name'), {
      target: { value: 'claude-on-opencode' },
    });
    fireEvent.change(screen.getByTestId('profile-model'), {
      target: { value: 'claude-sonnet-4' },
    });
    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(modelProfilesApi.create).mock
      .calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.provider).toBe('anthropic');
    expect(payload.connection_id).toBe('mcn_001');
  });

  it('编辑存量档案（connection_id=null）：默认落在「自定义配置」态，提交**不含** connection_id 键', async () => {
    vi.mocked(modelProfilesApi.list).mockResolvedValue([baseProfile]);
    vi.mocked(modelProfilesApi.update).mockResolvedValue(baseProfile);

    renderPage();

    fireEvent.click(await screen.findByTestId('model-profile-edit-mpf_001'));

    // 存量档案走自定义配置：地址 / 密钥输入框都在，连接下拉不存在
    expect(await screen.findByTestId('profile-base-url')).toBeInTheDocument();
    expect(screen.getByTestId('profile-api-key')).toBeInTheDocument();
    expect(screen.queryByTestId('profile-connection')).toBeNull();

    fireEvent.click(screen.getByTestId('profile-save'));

    await waitFor(() => {
      expect(modelProfilesApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(modelProfilesApi.update).mock.calls[0]!;
    // ⚠️ 用 hasOwnProperty 判「键不存在」——不能用 toBeUndefined
    // （键存在但值为 undefined 与键不存在是两件事）。
    expect(
      Object.prototype.hasOwnProperty.call(payload as object, 'connection_id'),
    ).toBe(false);
    // 既有行为不变：地址照旧在 params 里
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    expect(paramsOut).toHaveProperty('base_url', 'https://api.openai.com/v1');
  });

  it('解除连接关联：PATCH 只带 connection_id: null', async () => {
    vi.mocked(modelProfilesApi.list).mockResolvedValue([boundProfile]);
    vi.mocked(modelProfilesApi.update).mockResolvedValue(boundProfile);

    renderPage();

    fireEvent.click(await screen.findByTestId('model-profile-edit-mpf_bound'));
    fireEvent.click(await screen.findByTestId('profile-unbind-connection'));

    await waitFor(() => {
      expect(modelProfilesApi.update).toHaveBeenCalledTimes(1);
    });
    const [id, payload] = vi.mocked(modelProfilesApi.update).mock.calls[0]!;
    expect(id).toBe('mpf_bound');
    expect((payload as Record<string, unknown>).connection_id).toBeNull();
    expect(
      Object.prototype.hasOwnProperty.call(payload as object, 'connection_id'),
    ).toBe(true);
  });

  it('连接态：思考档位按**所选连接的地址**收窄（minimax 域 → default/off/adaptive）', async () => {
    const minimaxConn: ModelConnectionOut = {
      ...connFixture,
      connection_id: 'mcn_mini',
      name: 'minimax',
      base_url: 'https://api.minimax.chat/v1',
    };
    vi.mocked(connectionsApi.list).mockResolvedValue([connFixture, minimaxConn]);

    renderPage();

    fireEvent.click(await screen.findByTestId('new-model-profile'));
    fireEvent.click(await screen.findByTestId('profile-mode-connection'));
    const sel = (await screen.findByTestId('profile-connection')) as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: 'mcn_mini' } });

    const thinking = (await screen.findByTestId(
      'profile-thinking-select',
    )) as HTMLSelectElement;
    const values = Array.from(thinking.querySelectorAll('option')).map(
      (o) => (o as HTMLOptionElement).value,
    );
    expect(values).toEqual(['default', 'off', 'adaptive']);
  });

  it('自定义配置态行为不变：地址 / 密钥输入框仍在，provider 可选', async () => {
    renderPage();

    fireEvent.click(await screen.findByTestId('new-model-profile'));

    expect(await screen.findByTestId('profile-base-url')).toBeInTheDocument();
    expect(screen.getByTestId('profile-api-key')).toBeInTheDocument();
    expect(screen.getByTestId('profile-provider')).toBeInTheDocument();
    expect(screen.queryByTestId('profile-connection')).toBeNull();
  });
});

// =============================================================================
// 2026-10-23 修复「删除档案撞 409 死结」：候选链可见 / 可移除 / 改选保留 / 409 可读
//   背景：capability_bindings.profile_ids 是有序数组（顺序即 fallback 序，后端运行期
//   真会用后备位）。旧实现只渲染 profile_ids[0] ⇒ 后备成员看不见；改写下拉 = 整链
//   替换成单个 ⇒ 后备成员被静默丢弃 ⇒ 旧档案删不掉（后端 409 是真实状态）。
// =============================================================================

/** 后备档案：名字可辨识，便于断言「DOM 里出现的是档案名而不是裸 id」。 */
const fallbackProfile: ModelProfile = {
  ...baseProfile,
  profile_id: 'mpf_002',
  name: 'backup-writer',
  model: 'gpt-4o-mini',
};

/** 候选链夹具：正文写作 = [首选 mpf_001, 后备 mpf_002]；题材定位 = 单成员链。 */
const chainBindingsFixture: CapabilityBinding[] = [
  {
    capability: 'creative_writing',
    label: '正文写作',
    agents: ['writer'],
    profile_ids: ['mpf_001', 'mpf_002'],
    profiles: [
      { profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' },
      { profile_id: 'mpf_002', name: 'backup-writer', model: 'gpt-4o-mini' },
    ],
    legacy_available: false,
  },
  {
    capability: 'premise_design',
    label: '题材定位',
    agents: ['premise_designer'],
    profile_ids: ['mpf_001'],
    profiles: [{ profile_id: 'mpf_001', name: 'default-reasoning', model: 'gpt-4o-mini' }],
    legacy_available: false,
  },
];

describe('AiSettingsPage · 候选链（fallback 链）可见 / 可移除 / 409 可读', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(agentsApi.list).mockResolvedValue([baseAgent]);
    vi.mocked(agentsApi.listPrompts).mockResolvedValue([]);
    vi.mocked(modelProfilesApi.list).mockResolvedValue([baseProfile, fallbackProfile]);
    vi.mocked(capabilityBindingsApi.list).mockResolvedValue(chainBindingsFixture);
    vi.mocked(connectionsApi.list).mockResolvedValue([connFixture]);
  });

  it('T1 候选链可见：后备成员 mpf_002 连同档案名一起渲染（不是只渲染首选）', async () => {
    renderPage();
    await screen.findByTestId('capability-bindings-list');

    // 后备成员在 DOM 里可见，且显示的是档案名（不是裸 id）。
    const name = screen.getByTestId('cap-binding-chain-name-creative_writing-mpf_002');
    expect(name).toHaveTextContent('backup-writer');
    expect(name).toHaveTextContent('gpt-4o-mini');

    // 顺序即 fallback 序：首选在前、后备在后。
    const chain = screen.getByTestId('cap-binding-chain-creative_writing');
    const order = Array.from(
      chain.querySelectorAll('[data-testid^="cap-binding-chain-item-"]'),
    ).map((el) => el.getAttribute('data-testid'));
    expect(order).toEqual([
      'cap-binding-chain-item-creative_writing-mpf_001',
      'cap-binding-chain-item-creative_writing-mpf_002',
    ]);
  });

  it('T2 移除后备成员：提交「去掉该成员后的有序剩余列表」，不是整链原样提交', async () => {
    vi.mocked(capabilityBindingsApi.bind).mockResolvedValue(chainBindingsFixture[0]!);

    renderPage();
    await screen.findByTestId('capability-bindings-list');

    fireEvent.click(
      screen.getByTestId('cap-binding-chain-remove-creative_writing-mpf_002'),
    );

    await waitFor(() => {
      expect(capabilityBindingsApi.bind).toHaveBeenCalledTimes(1);
    });
    const [capability, profileIds] = vi.mocked(capabilityBindingsApi.bind).mock.calls[0]!;
    expect(capability).toBe('creative_writing');
    // 逐位相等：只剩首选（不能把 ['mpf_001','mpf_002'] 原样提交回去）。
    expect(profileIds).toEqual(['mpf_001']);
  });

  it('T3 移除最后一个成员：链空 ⇒ 走 unbind，不发 bind（后端 PUT 对空数组返 422）', async () => {
    vi.mocked(capabilityBindingsApi.unbind).mockResolvedValue(undefined as never);

    renderPage();
    await screen.findByTestId('capability-bindings-list');

    fireEvent.click(
      screen.getByTestId('cap-binding-chain-remove-premise_design-mpf_001'),
    );

    await waitFor(() => {
      expect(capabilityBindingsApi.unbind).toHaveBeenCalledTimes(1);
    });
    expect(capabilityBindingsApi.unbind).toHaveBeenCalledWith('premise_design');
    expect(capabilityBindingsApi.bind).not.toHaveBeenCalled();
  });

  it('T4 下拉改选 = 设为首选 + 其余成员按原相对顺序保留', async () => {
    vi.mocked(capabilityBindingsApi.bind).mockResolvedValue(chainBindingsFixture[0]!);

    renderPage();
    await screen.findByTestId('capability-bindings-list');

    const sel = screen.getByTestId(
      'cap-binding-select-creative_writing',
    ) as HTMLSelectElement;
    // 原首选是 mpf_001；改成 mpf_002 ⇒ 新首选在前，原首选退到第 2 位（不丢成员）。
    fireEvent.change(sel, { target: { value: 'mpf_002' } });

    await waitFor(() => {
      expect(capabilityBindingsApi.bind).toHaveBeenCalledTimes(1);
    });
    const [capability, profileIds] = vi.mocked(capabilityBindingsApi.bind).mock.calls[0]!;
    expect(capability).toBe('creative_writing');
    expect(profileIds).toEqual(['mpf_002', 'mpf_001']);
  });

  it('T5 删除档案撞 409：显示 message 原文 + 环节中文名，不甩原始 JSON 串', async () => {
    vi.mocked(modelProfilesApi.remove).mockRejectedValue(
      new ApiError(
        409,
        JSON.stringify({
          error: 'profile_in_use',
          message: "profile 'x' 被 1 个 binding 引用",
          referenced_by: ['creative_writing'],
        }),
      ),
    );

    renderPage();
    fireEvent.click(await screen.findByTestId('model-profile-delete-mpf_001'));
    fireEvent.click(await screen.findByTestId('model-profile-delete-confirm-confirm'));

    const err = await screen.findByTestId('model-profile-test-result');
    // 后端 message 原文可见。
    expect(err).toHaveTextContent("profile 'x' 被 1 个 binding 引用");
    // capability 映射成中文 label（不是 creative_writing 裸串）。
    expect(err).toHaveTextContent('正文写作');
    // 不能是原始 JSON 串，也不能是 [object Object]。
    expect(err.textContent).not.toContain('"referenced_by"');
    expect(err.textContent).not.toContain('[object Object]');
    // 指引必须指向用户真做得到的动作。
    expect(err).toHaveTextContent('环节分配');
  });
});
