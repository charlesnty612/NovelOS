import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AiSettingsPage } from './AiSettingsPage';
import { agentsApi, modelConfigsApi } from '../api/endpoints';
import type { Agent, ModelConfig } from '../api/types';

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
    modelConfigsApi: {
      list: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      test: vi.fn(),
    },
  };
});

const baseConfig: ModelConfig = {
  config_id: 'mcf_001',
  capability: 'reasoning',
  provider: 'openai_compatible',
  model: 'gpt-4o-mini',
  params_json: {
    base_url: 'https://api.openai.com/v1',
    api_key: '***',  // 已脱敏
  },
  enabled: 1,
  has_api_key: true,
};

const configNoKey: ModelConfig = {
  ...baseConfig,
  config_id: 'mcf_002',
  params_json: { base_url: 'https://api.openai.com/v1' },
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

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/projects/test-project/ai-settings']}>
      <AiSettingsPage />
    </MemoryRouter>,
  );
}

describe('AiSettingsPage · 清除已存密钥', () => {
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
  });

  it('has_api_key=true 时显示「清除已存密钥」按钮', async () => {
    vi.mocked(modelConfigsApi.list).mockResolvedValue([baseConfig]);
    vi.mocked(modelConfigsApi.get).mockResolvedValue(baseConfig);
    vi.mocked(modelConfigsApi.update).mockResolvedValue({
      ...baseConfig,
      has_api_key: false,
    });

    renderPage();

    // 进入编辑模式：点击「编辑」
    const editBtn = await screen.findByTestId('model-config-edit-mcf_001');
    fireEvent.click(editBtn);

    const clearBtn = await screen.findByTestId('cfg-clear-api-key');
    expect(clearBtn).toBeInTheDocument();
    expect(clearBtn).toBeEnabled();
    expect(clearBtn).toHaveTextContent('清除已存密钥');
  });

  it('点击「清除已存密钥」后保存 → PATCH body 含 api_key=""', async () => {
    vi.mocked(modelConfigsApi.list).mockResolvedValue([baseConfig]);
    vi.mocked(modelConfigsApi.get).mockResolvedValue(baseConfig);
    vi.mocked(modelConfigsApi.update).mockResolvedValue({
      ...baseConfig,
      has_api_key: false,
    });

    renderPage();

    const editBtn = await screen.findByTestId('model-config-edit-mcf_001');
    fireEvent.click(editBtn);

    const clearBtn = await screen.findByTestId('cfg-clear-api-key');
    fireEvent.click(clearBtn);

    // 点击后按钮文案变 "已请求清除" 且 disabled
    await waitFor(() => {
      expect(clearBtn).toHaveTextContent('已请求清除');
    });
    expect(clearBtn).toBeDisabled();

    // 提交
    const saveBtn = screen.getByTestId('cfg-save');
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(modelConfigsApi.update).toHaveBeenCalledTimes(1);
    });
    const callArgs = vi.mocked(modelConfigsApi.update).mock.calls[0];
    expect(callArgs[0]).toBe('mcf_001');
    const payload = callArgs[1] as Record<string, unknown>;
    expect(payload).toMatchObject({
      capability: 'reasoning',
      provider: 'openai_compatible',
      model: 'gpt-4o-mini',
    });
    const params_json = payload.params_json as Record<string, unknown>;
    expect(params_json).toHaveProperty('api_key', '');
    // base_url 必须保留
    expect(params_json).toHaveProperty('base_url', 'https://api.openai.com/v1');
  });

  it('has_api_key=false 时不显示「清除已存密钥」按钮', async () => {
    vi.mocked(modelConfigsApi.list).mockResolvedValue([configNoKey]);
    vi.mocked(modelConfigsApi.get).mockResolvedValue(configNoKey);

    renderPage();

    const editBtn = await screen.findByTestId('model-config-edit-mcf_002');
    fireEvent.click(editBtn);

    // 等对话框渲染完成
    await screen.findByTestId('cfg-api-key');
    expect(screen.queryByTestId('cfg-clear-api-key')).toBeNull();
  });

  it('点击「清除已存密钥」后用户重新填入新值 → 取消清除请求', async () => {
    vi.mocked(modelConfigsApi.list).mockResolvedValue([baseConfig]);
    vi.mocked(modelConfigsApi.get).mockResolvedValue(baseConfig);
    vi.mocked(modelConfigsApi.update).mockResolvedValue(baseConfig);

    renderPage();

    const editBtn = await screen.findByTestId('model-config-edit-mcf_001');
    fireEvent.click(editBtn);

    const clearBtn = await screen.findByTestId('cfg-clear-api-key');
    fireEvent.click(clearBtn);

    // 重新输入新值
    const apiKeyInput = screen.getByTestId('cfg-api-key') as HTMLInputElement;
    fireEvent.change(apiKeyInput, { target: { value: 'new-secret-xyz' } });

    // 按钮恢复可用，文案回到「清除已存密钥」
    await waitFor(() => {
      expect(clearBtn).toHaveTextContent('清除已存密钥');
    });
    expect(clearBtn).toBeEnabled();

    // 提交后 PATCH body 含新值（非空串）
    fireEvent.click(screen.getByTestId('cfg-save'));
    await waitFor(() => {
      expect(modelConfigsApi.update).toHaveBeenCalledTimes(1);
    });
    const params_json = (vi.mocked(modelConfigsApi.update).mock
      .calls[0][1] as Record<string, unknown>).params_json as Record<string, unknown>;
    expect(params_json).toHaveProperty('api_key', 'new-secret-xyz');
  });

  it('未点击清除按钮时，提交不应传 api_key 字段（保留 DB 原值）', async () => {
    vi.mocked(modelConfigsApi.list).mockResolvedValue([baseConfig]);
    vi.mocked(modelConfigsApi.get).mockResolvedValue(baseConfig);
    vi.mocked(modelConfigsApi.update).mockResolvedValue(baseConfig);

    renderPage();

    const editBtn = await screen.findByTestId('model-config-edit-mcf_001');
    fireEvent.click(editBtn);

    // 等待 modal 完整渲染（cfg-save 与 cfg-clear-api-key 都出现）
    await screen.findByTestId('cfg-clear-api-key');
    const saveBtn = await screen.findByTestId('cfg-save');

    // 不点清除，直接保存
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(modelConfigsApi.update).toHaveBeenCalledTimes(1);
    });
    const params_json = (vi.mocked(modelConfigsApi.update).mock
      .calls[0][1] as Record<string, unknown>).params_json as Record<string, unknown>;
    expect(params_json).not.toHaveProperty('api_key');
    expect(params_json).toHaveProperty('base_url', 'https://api.openai.com/v1');
  });
});