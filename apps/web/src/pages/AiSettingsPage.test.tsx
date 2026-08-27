import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AiSettingsPage } from './AiSettingsPage';
import { agentsApi, capabilityBindingsApi, modelProfilesApi } from '../api/endpoints';
import type {
  Agent,
  CapabilityBinding,
  ModelProfile,
} from '../api/types';

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
    },
    capabilityBindingsApi: {
      list: vi.fn(),
      bind: vi.fn(),
      unbind: vi.fn(),
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
    agents: ['director', 'observer', 'arbiter'],
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

  it('环节分配渲染 7 行（含中文 label + agents 小字）', async () => {
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
    // 7 行
    const rows = document.querySelectorAll(
      '[data-testid^="cap-binding-"]:not([data-testid*="-select"]):not([data-testid*="-legacy"]):not([data-testid*="-saving"]):not([data-testid*="-saved"]):not([data-testid*="-error"]):not([data-testid*="-status"])',
    );
    // 过滤得到「cap-binding-{capability}」的行
    const fixed = bindingsFixture.filter((b) =>
      document.querySelector(`[data-testid="cap-binding-${b.capability}"]`),
    );
    expect(fixed).toHaveLength(7);
    // sanity: 至少查到 7 个匹配
    expect(rows.length).toBeGreaterThanOrEqual(7);
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
});