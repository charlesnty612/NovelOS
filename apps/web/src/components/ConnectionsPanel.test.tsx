import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ConnectionsPanel } from './ConnectionsPanel';
import { connectionsApi, modelProfilesApi } from '../api/endpoints';
import { ApiError } from '../api/client';
import type { ConnectionPresetOut, ModelConnectionOut } from '../api/types';

// ⚠️ 显式覆盖 connectionsApi / modelProfilesApi：mock 工厂里的 `...actual`
// 会把**真实实现**带进来（真实 fetch）⇒ 测试会发真请求。
vi.mock('../api/endpoints', async () => {
  const actual =
    await vi.importActual<typeof import('../api/endpoints')>('../api/endpoints');
  return {
    ...actual,
    connectionsApi: {
      list: vi.fn(),
      presets: vi.fn(),
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      test: vi.fn(),
    },
    modelProfilesApi: {
      list: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      test: vi.fn(),
      fetchAvailableModels: vi.fn(),
    },
  };
});

const conn: ModelConnectionOut = {
  connection_id: 'mcn_001',
  name: 'opencode',
  provider: 'openai_compatible',
  base_url: 'https://opencode.ai/inference/go/v1',
  // 读路径：api_key 被脱敏成 "***"；headers 里的非凭据头保持明文。
  params_json: {
    api_key: '***',
    headers: { 'x-opencode-session': '{{session_id}}' },
  },
  enabled: 1,
  created_at: '2026-10-21T10:00:00+00:00',
  updated_at: '2026-10-21T10:00:00+00:00',
  has_api_key: true,
};

const connNoKey: ModelConnectionOut = {
  ...conn,
  connection_id: 'mcn_002',
  name: 'ollama-local',
  provider: 'ollama',
  base_url: 'http://127.0.0.1:11434',
  params_json: {},
  has_api_key: false,
  enabled: 0,
};

function renderPanel() {
  return render(<ConnectionsPanel />);
}

/** 厂商预设夹具（与后端 presets.py 的清单同形；opencode_go 带协议地址池）。 */
const presetFixture: ConnectionPresetOut[] = [
  {
    preset_id: 'opencode_go',
    label: 'opencode Go（订阅制）',
    provider: 'openai_compatible',
    base_url: 'https://opencode.ai/inference/go/openai/v1',
    protocol_base_urls: {
      openai_compatible: 'https://opencode.ai/inference/go/openai/v1',
      anthropic: 'https://opencode.ai/inference/go/anthropic',
    },
    params: {
      headers: { 'x-opencode-session': '{{session_id}}' },
      models_url: 'https://opencode.ai/inference/go/v1/models',
    },
    note: '填好 API 密钥（oc_sk_…）即可用；会话 id 由 NovelOS 按写作 run 自动派生，无需自己填',
  },
];

describe('ConnectionsPanel · 模型连接', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(connectionsApi.list).mockResolvedValue([conn, connNoKey]);
    vi.mocked(connectionsApi.presets).mockResolvedValue(presetFixture);
    vi.mocked(modelProfilesApi.create).mockResolvedValue({
      profile_id: 'mpf_new',
      name: 'opencode / gpt-4o-mini',
      provider: 'openai_compatible',
      model: 'gpt-4o-mini',
      params: {},
      enabled: 1,
      has_api_key: false,
      connection_id: 'mcn_001',
    });
  });

  // -------------------- 列表渲染 --------------------------------------------

  it('渲染连接行：名称 / provider / 地址 / 密钥徽标 / 启停徽标', async () => {
    renderPanel();

    const row = await screen.findByTestId('connection-row-mcn_001');
    expect(row).toHaveTextContent('opencode');
    expect(row).toHaveTextContent('OpenAI 兼容');
    expect(row).toHaveTextContent('https://opencode.ai/inference/go/v1');
    expect(screen.getByTestId('connection-key-badge-mcn_001')).toHaveTextContent(
      '已配置密钥',
    );
    expect(screen.getByTestId('connection-enabled-badge-mcn_001')).toHaveTextContent('启用');
  });

  it('未配置密钥 / 停用的连接显示对应徽标', async () => {
    renderPanel();

    expect(
      await screen.findByTestId('connection-key-badge-mcn_002'),
    ).toHaveTextContent('未配置密钥');
    expect(screen.getByTestId('connection-enabled-badge-mcn_002')).toHaveTextContent('停用');
  });

  // -------------------- 痛点正对：「+ 模型」只填模型名 ------------------------

  it('「+ 模型」：只填模型名就建出档案（带 connection_id 与连接的 provider）', async () => {
    renderPanel();

    fireEvent.click(await screen.findByTestId('connection-add-model-mcn_001'));

    // 弹窗里**只有**模型名与档案名两个输入框 —— 没有地址 / 密钥输入框。
    const modal = await screen.findByTestId('connection-add-model-modal');
    expect(modal.querySelector('[data-testid="connection-base-url"]')).toBeNull();
    expect(modal.querySelector('[data-testid="connection-api-key"]')).toBeNull();

    fireEvent.change(screen.getByTestId('add-model-name'), {
      target: { value: 'gpt-4o-mini' },
    });
    // 档案名默认「<连接名> / <模型名>」
    const nameInput = screen.getByTestId('add-model-profile-name') as HTMLInputElement;
    expect(nameInput.value).toBe('opencode / gpt-4o-mini');

    fireEvent.click(screen.getByTestId('add-model-save'));

    await waitFor(() => {
      expect(modelProfilesApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(modelProfilesApi.create).mock
      .calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.connection_id).toBe('mcn_001');
    expect(payload.model).toBe('gpt-4o-mini');
    expect(payload.name).toBe('opencode / gpt-4o-mini');
    // provider 取自连接（连接是唯一权威）
    expect(payload.provider).toBe('openai_compatible');
    // 档案级不再重复携带地址 / 密钥
    const paramsOut = payload.params as Record<string, unknown>;
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'base_url')).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(paramsOut, 'api_key')).toBe(false);
  });

  it('「+ 模型」：手改档案名后按手改值提交', async () => {
    renderPanel();

    fireEvent.click(await screen.findByTestId('connection-add-model-mcn_001'));
    fireEvent.change(screen.getByTestId('add-model-name'), {
      target: { value: 'gpt-4o-mini' },
    });
    fireEvent.change(screen.getByTestId('add-model-profile-name'), {
      target: { value: '我的写作模型' },
    });
    fireEvent.click(screen.getByTestId('add-model-save'));

    await waitFor(() => {
      expect(modelProfilesApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(modelProfilesApi.create).mock
      .calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.name).toBe('我的写作模型');
  });

  it('「+ 模型」：模型名为空时拦下，不发请求', async () => {
    renderPanel();

    fireEvent.click(await screen.findByTestId('connection-add-model-mcn_001'));
    await screen.findByTestId('add-model-save');
    fireEvent.click(screen.getByTestId('add-model-save'));

    await waitFor(() => {
      expect(screen.getByTestId('connection-add-model-modal')).toHaveTextContent(
        '模型名不能为空',
      );
    });
    expect(modelProfilesApi.create).not.toHaveBeenCalled();
  });

  // -------------------- 测试连接 --------------------------------------------

  it('测试连接成功：信息通道（alert--info）+ 显示 ok / detail / probe', async () => {
    vi.mocked(connectionsApi.test).mockResolvedValue({
      connection_id: 'mcn_001',
      ok: true,
      latency_ms: 42,
      detail: 'reachable',
      status_code: 200,
      probe: 'models',
    });

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-test-mcn_001'));

    const res = await screen.findByTestId('connection-test-result');
    expect(res.closest('.alert')?.className).toContain('alert--info');
    expect(res).toHaveTextContent('成功');
    expect(res).toHaveTextContent('reachable');
    expect(res).toHaveTextContent('models');
  });

  it('测试连接失败：错误通道（alert--error）', async () => {
    vi.mocked(connectionsApi.test).mockRejectedValue(
      new ApiError(500, 'network error'),
    );

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-test-mcn_001'));

    const res = await screen.findByTestId('connection-test-result');
    expect(res.closest('.alert')?.className).toContain('alert--error');
    expect(res).toHaveTextContent('失败');
  });

  // -------------------- 删除 ------------------------------------------------

  it('删除：二次确认后才真的调 DELETE', async () => {
    vi.mocked(connectionsApi.remove).mockResolvedValue(undefined as never);

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-delete-mcn_001'));

    // 二次确认弹窗出现，尚未发请求
    await screen.findByTestId('model-connection-delete-confirm');
    expect(connectionsApi.remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('model-connection-delete-confirm-confirm'));

    await waitFor(() => {
      expect(connectionsApi.remove).toHaveBeenCalledTimes(1);
    });
    expect(connectionsApi.remove).toHaveBeenCalledWith('mcn_001');
  });

  it('删除被引用的连接（409）：展示引用它的档案 id，不静默失败', async () => {
    vi.mocked(connectionsApi.remove).mockRejectedValue(
      new ApiError(
        409,
        JSON.stringify({
          error: 'connection_in_use',
          message: "connection 'mcn_001' 被 2 个档案引用",
          referenced_by: ['mpf_001', 'mpf_002'],
        }),
      ),
    );

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-delete-mcn_001'));
    fireEvent.click(
      await screen.findByTestId('model-connection-delete-confirm-confirm'),
    );

    const err = await screen.findByTestId('model-connection-delete-error');
    expect(err).toHaveTextContent('mpf_001');
    expect(err).toHaveTextContent('mpf_002');
  });

  // -------------------- 新建 / 编辑连接 --------------------------------------

  it('新建连接：提交名称 / provider / 地址 / 密钥 / 头 / 模型列表地址', async () => {
    vi.mocked(connectionsApi.create).mockResolvedValue(conn);

    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    fireEvent.change(screen.getByTestId('connection-name'), {
      target: { value: '主力厂商' },
    });
    fireEvent.change(screen.getByTestId('connection-base-url'), {
      target: { value: 'https://api.example.com/v1' },
    });
    fireEvent.change(screen.getByTestId('connection-api-key'), {
      target: { value: 'sk-plain' },
    });
    fireEvent.click(screen.getByTestId('connection-header-add'));
    fireEvent.change(screen.getByTestId('connection-header-key-0'), {
      target: { value: 'x-opencode-session' },
    });
    fireEvent.change(screen.getByTestId('connection-header-value-0'), {
      target: { value: '{{session_id}}' },
    });
    fireEvent.change(screen.getByTestId('connection-models-url'), {
      target: { value: 'https://api.example.com/v1/models' },
    });

    fireEvent.click(screen.getByTestId('connection-save'));

    await waitFor(() => {
      expect(connectionsApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(connectionsApi.create).mock
      .calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.name).toBe('主力厂商');
    expect(payload.provider).toBe('openai_compatible');
    expect(payload.base_url).toBe('https://api.example.com/v1');
    const paramsOut = payload.params as Record<string, unknown>;
    expect(paramsOut.api_key).toBe('sk-plain');
    expect(paramsOut.headers).toEqual({
      'x-opencode-session': '{{session_id}}',
    });
    expect(paramsOut.models_url).toBe('https://api.example.com/v1/models');
    // 空池（未添加任何协议地址行）⇒ 不发 protocol_base_urls 字段（缺省 = 不动）
    expect(
      Object.prototype.hasOwnProperty.call(payload, 'protocol_base_urls'),
    ).toBe(false);
  });

  it('编辑连接：密钥输入框不回填 "***"；「清除已存密钥」显式发 api_key=""', async () => {
    vi.mocked(connectionsApi.update).mockResolvedValue(conn);

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-edit-mcn_001'));

    const apiKeyInput = (await screen.findByTestId(
      'connection-api-key',
    )) as HTMLInputElement;
    expect(apiKeyInput.value).toBe('');
    expect(apiKeyInput.value).not.toContain('***');

    // 已存的自定义头原样回显（敏感头的值保持 "***"，提交即「不改」）
    const headerValue = (await screen.findByTestId(
      'connection-header-value-0',
    )) as HTMLInputElement;
    expect(headerValue.value).toBe('{{session_id}}');

    fireEvent.click(screen.getByTestId('connection-clear-api-key'));
    fireEvent.click(screen.getByTestId('connection-save'));

    await waitFor(() => {
      expect(connectionsApi.update).toHaveBeenCalledTimes(1);
    });
    const [id, payload] = vi.mocked(connectionsApi.update).mock.calls[0]!;
    expect(id).toBe('mcn_001');
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    expect(paramsOut.api_key).toBe('');
  });

  it('编辑连接：删掉一行自定义头 ⇒ 该键显式发 null（后端逐键删除语义）', async () => {
    vi.mocked(connectionsApi.update).mockResolvedValue(conn);

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-edit-mcn_001'));
    await screen.findByTestId('connection-header-remove-0');
    fireEvent.click(screen.getByTestId('connection-header-remove-0'));
    fireEvent.click(screen.getByTestId('connection-save'));

    await waitFor(() => {
      expect(connectionsApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(connectionsApi.update).mock.calls[0]!;
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    expect(paramsOut.headers).toEqual({ 'x-opencode-session': null });
  });

  it('编辑连接：敏感头的值是 "***" ⇒ 原样回显并原样提交（后端逐键保留，不写坏真值）', async () => {
    const withSecretHeader: ModelConnectionOut = {
      ...conn,
      params_json: {
        api_key: '***',
        headers: { authorization: '***', 'x-opencode-session': '{{session_id}}' },
      },
    };
    vi.mocked(connectionsApi.list).mockResolvedValue([withSecretHeader]);
    vi.mocked(connectionsApi.update).mockResolvedValue(withSecretHeader);

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-edit-mcn_001'));

    const authValue = (await screen.findByTestId(
      'connection-header-value-0',
    )) as HTMLInputElement;
    expect(authValue.value).toBe('***');

    fireEvent.click(screen.getByTestId('connection-save'));

    await waitFor(() => {
      expect(connectionsApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(connectionsApi.update).mock.calls[0]!;
    const paramsOut = (payload as Record<string, unknown>).params as Record<string, unknown>;
    // ⚠️ 必须是 "***"（= 不改这个键），不能是 ''（= 删除该键）
    expect(paramsOut.headers).toEqual({
      authorization: '***',
      'x-opencode-session': '{{session_id}}',
    });
  });

  // -------------------- 厂商预设（一键填表）------------------------------------


  it('选「opencode Go」预设 ⇒ 自动填接口地址与请求头，并显示提示', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    // 判别点：预设下拉**自己的** data-testid + 被填进表单的输入框。
    const presetSelect = (await screen.findByTestId(
      'connection-preset',
    )) as HTMLSelectElement;
    await waitFor(() => {
      const opts = Array.from(presetSelect.querySelectorAll('option')).map(
        (o) => (o as HTMLOptionElement).value,
      );
      expect(opts).toContain('opencode_go');
    });

    fireEvent.change(presetSelect, { target: { value: 'opencode_go' } });

    // 接口地址被填成预设里的地址
    await waitFor(() => {
      expect(
        (screen.getByTestId('connection-base-url') as HTMLInputElement).value,
      ).toBe('https://opencode.ai/inference/go/openai/v1');
    });
    // 请求头里有 x-opencode-session
    const headerKey = (await screen.findByTestId(
      'connection-header-key-0',
    )) as HTMLInputElement;
    expect(headerKey.value).toBe('x-opencode-session');
    const headerValue = screen.getByTestId('connection-header-value-0') as HTMLInputElement;
    expect(headerValue.value).toBe('{{session_id}}');
    // 模型列表地址也被填上
    expect(
      (screen.getByTestId('connection-models-url') as HTMLInputElement).value,
    ).toBe('https://opencode.ai/inference/go/v1/models');
    // 提示文案来自预设的 note（会话 id 由系统派生，用户不用自己填请求头）
    const note = await screen.findByTestId('connection-preset-note');
    expect(note).toHaveTextContent('会话 id');
  });

  it('选预设不动密钥框；切回「无预设 / 自定义」清掉自动填的地址与头但保留密钥', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    const presetSelect = (await screen.findByTestId(
      'connection-preset',
    )) as HTMLSelectElement;
    await waitFor(() => {
      const opts = Array.from(presetSelect.querySelectorAll('option')).map(
        (o) => (o as HTMLOptionElement).value,
      );
      expect(opts).toContain('opencode_go');
    });

    // 用户先手填密钥
    fireEvent.change(screen.getByTestId('connection-api-key'), {
      target: { value: 'oc_sk-user' },
    });

    fireEvent.change(presetSelect, { target: { value: 'opencode_go' } });
    await waitFor(() => {
      expect(
        (screen.getByTestId('connection-base-url') as HTMLInputElement).value,
      ).toBe('https://opencode.ai/inference/go/openai/v1');
    });
    // 预设不覆盖密钥框
    expect((screen.getByTestId('connection-api-key') as HTMLInputElement).value).toBe(
      'oc_sk-user',
    );

    // 切回「无预设 / 自定义」⇒ 自动填的地址 / 头 / 协议池被清掉，密钥保留
    fireEvent.change(presetSelect, { target: { value: '' } });
    await waitFor(() => {
      expect(
        (screen.getByTestId('connection-base-url') as HTMLInputElement).value,
      ).toBe('');
    });
    expect(screen.queryByTestId('connection-header-key-0')).toBeNull();
    expect(screen.queryByTestId('connection-pool-protocol-0')).toBeNull();
    expect((screen.getByTestId('connection-api-key') as HTMLInputElement).value).toBe(
      'oc_sk-user',
    );
  });

  // -------------------- 协议地址池（2026-10-24，协议下放模型档案）--------------

  it('连接表单不再有「接口类型」下拉（协议是档案级属性，判别：connection-provider 为 null）', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    await screen.findByTestId('connection-name');
    // ⚠️ 判别点：旧表单这里有「接口类型 *」下拉（data-testid="connection-provider"），
    // 协议下放档案层后整个字段必须消失。
    expect(screen.queryByTestId('connection-provider')).toBeNull();
  });

  it('选「opencode Go」预设 ⇒ 协议地址池自动出现两行（openai_compatible + anthropic 地址逐字）', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    const presetSelect = (await screen.findByTestId(
      'connection-preset',
    )) as HTMLSelectElement;
    await waitFor(() => {
      const opts = Array.from(presetSelect.querySelectorAll('option')).map(
        (o) => (o as HTMLOptionElement).value,
      );
      expect(opts).toContain('opencode_go');
    });

    fireEvent.change(presetSelect, { target: { value: 'opencode_go' } });

    const proto0 = (await screen.findByTestId(
      'connection-pool-protocol-0',
    )) as HTMLSelectElement;
    expect(proto0.value).toBe('openai_compatible');
    expect(
      (screen.getByTestId('connection-pool-url-0') as HTMLInputElement).value,
    ).toBe('https://opencode.ai/inference/go/openai/v1');

    const proto1 = screen.getByTestId(
      'connection-pool-protocol-1',
    ) as HTMLSelectElement;
    expect(proto1.value).toBe('anthropic');
    expect(
      (screen.getByTestId('connection-pool-url-1') as HTMLInputElement).value,
    ).toBe('https://opencode.ai/inference/go/anthropic');
  });

  it('手动添加协议地址行 ⇒ 汇总进提交体 protocol_base_urls（逐字）', async () => {
    vi.mocked(connectionsApi.create).mockResolvedValue(conn);

    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    fireEvent.change(screen.getByTestId('connection-name'), {
      target: { value: '多协议网关' },
    });
    fireEvent.change(screen.getByTestId('connection-base-url'), {
      target: { value: 'https://gateway.example.com/v1' },
    });
    fireEvent.click(screen.getByTestId('connection-pool-add'));
    fireEvent.change(screen.getByTestId('connection-pool-protocol-0'), {
      target: { value: 'anthropic' },
    });
    fireEvent.change(screen.getByTestId('connection-pool-url-0'), {
      target: { value: 'https://gateway.example.com/anthropic' },
    });

    fireEvent.click(screen.getByTestId('connection-save'));

    await waitFor(() => {
      expect(connectionsApi.create).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(connectionsApi.create).mock
      .calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.protocol_base_urls).toEqual({
      anthropic: 'https://gateway.example.com/anthropic',
    });
  });

  it('协议地址池有重复协议 ⇒ 红色提示禁保存，不发请求', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    fireEvent.change(screen.getByTestId('connection-name'), {
      target: { value: '重复协议' },
    });
    fireEvent.change(screen.getByTestId('connection-base-url'), {
      target: { value: 'https://gateway.example.com/v1' },
    });
    fireEvent.click(screen.getByTestId('connection-pool-add'));
    fireEvent.change(screen.getByTestId('connection-pool-protocol-0'), {
      target: { value: 'anthropic' },
    });
    fireEvent.change(screen.getByTestId('connection-pool-url-0'), {
      target: { value: 'https://gateway.example.com/anthropic' },
    });
    fireEvent.click(screen.getByTestId('connection-pool-add'));
    fireEvent.change(screen.getByTestId('connection-pool-protocol-1'), {
      target: { value: 'anthropic' },
    });
    fireEvent.change(screen.getByTestId('connection-pool-url-1'), {
      target: { value: 'https://gateway.example.com/anthropic2' },
    });

    fireEvent.click(screen.getByTestId('connection-save'));

    const modal = await screen.findByTestId('model-connection-form-modal');
    await waitFor(() => {
      expect(modal).toHaveTextContent('协议地址池里有重复的协议：anthropic');
    });
    expect(connectionsApi.create).not.toHaveBeenCalled();
  });

  it('主接口地址为空 ⇒ 必填拦下，不发请求（连接必须知道默认连到哪）', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('new-model-connection'));

    fireEvent.change(screen.getByTestId('connection-name'), {
      target: { value: '没有地址' },
    });
    // 接口地址留空
    fireEvent.click(screen.getByTestId('connection-save'));

    const modal = await screen.findByTestId('model-connection-form-modal');
    await waitFor(() => {
      expect(modal).toHaveTextContent('接口地址不能为空');
    });
    expect(connectionsApi.create).not.toHaveBeenCalled();
  });

  it('编辑连接：协议池按连接回填；删掉行 ⇒ 整表替换语义（全删 ⇒ 显式发 null 清空）', async () => {
    const connWithPool: ModelConnectionOut = {
      ...conn,
      protocol_base_urls: {
        openai_compatible: 'https://opencode.ai/inference/go/openai/v1',
        anthropic: 'https://opencode.ai/inference/go/anthropic',
      },
    };
    vi.mocked(connectionsApi.list).mockResolvedValue([connWithPool]);
    vi.mocked(connectionsApi.update).mockResolvedValue(connWithPool);

    renderPanel();
    fireEvent.click(await screen.findByTestId('connection-edit-mcn_001'));

    // 池按连接回填（两行、地址逐字）
    const proto0 = (await screen.findByTestId(
      'connection-pool-protocol-0',
    )) as HTMLSelectElement;
    expect(proto0.value).toBe('openai_compatible');
    expect(
      (screen.getByTestId('connection-pool-url-1') as HTMLInputElement).value,
    ).toBe('https://opencode.ai/inference/go/anthropic');

    // 全部删除 ⇒ 保存时显式发 protocol_base_urls: null（后端清空语义，全部走主地址）
    fireEvent.click(screen.getByTestId('connection-pool-remove-0'));
    fireEvent.click(screen.getByTestId('connection-pool-remove-0'));
    fireEvent.click(screen.getByTestId('connection-save'));

    await waitFor(() => {
      expect(connectionsApi.update).toHaveBeenCalledTimes(1);
    });
    const [, payload] = vi.mocked(connectionsApi.update).mock.calls[0]!;
    expect((payload as Record<string, unknown>).protocol_base_urls).toBeNull();
  });
});
