import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  agentsApi,
  modelConfigsApi,
} from '../api/endpoints';
import type {
  Agent,
  ModelConfig,
  ModelConfigCreatePayload,
  ModelConfigUpdatePayload,
  PromptVersion,
  SyncPromptsResult,
} from '../api/types';
import { ErrorBanner, InfoBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { useApiCall } from '../hooks/useApiCall';
import { formatDateTime, formatJson, tryParseJsonObject } from '../utils/format';
import { ApiError } from '../api/client';

const PROVIDER_OPTIONS = [
  { value: 'openai_compatible', label: 'OpenAI 兼容（OpenAI / DeepSeek / Ollama 等）' },
  { value: 'anthropic', label: 'Anthropic（Claude 原生 Messages API）' },
  { value: 'ollama', label: 'Ollama（本地 /api/chat，无需密钥）' },
  { value: 'mock', label: 'Mock（本地兜底）' },
];

const CAPABILITY_OPTIONS = [
  { value: 'reasoning', label: 'reasoning（director / observer / arbiter）' },
  { value: 'creative_writing', label: 'creative_writing（writer）' },
];

export function AiSettingsPage() {
  return (
    <div>
      <h1 className="section-title">AI 设置</h1>
      <p className="section-subtitle">
        模型路由（按 capability 选 provider）与 Agent prompt 版本管理。本期页面挂在项目
        路由下，仅展示该 scope 内可配置项；模型配置是项目无关的全局表，这里仍展示完整列表。
      </p>

      <div className="panel-grid">
        <ModelConfigsPanel />
        <AgentsPanel />
      </div>
    </div>
  );
}

// =============================================================================
// ModelConfigsPanel
// =============================================================================

function ModelConfigsPanel() {
  const { pid } = useParams();
  void pid;
  const list = useApiCall<ModelConfig[]>(
    () => modelConfigsApi.list(),
    [],
  );
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [creating, setCreating] = useState(false);
  const [testResult, setTestResult] = useState<{
    config_id: string;
    ok: boolean;
    latency_ms: number;
    detail: string;
    status_code: number | null;
  } | null>(null);

  return (
    <div className="panel" data-testid="model-configs-panel">
      <div className="panel__title">
        模型配置（model_configs）
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm btn--primary"
          onClick={() => setCreating(true)}
          data-testid="new-model-config"
        >
          + 新建
        </button>
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>

      {list.loading ? (
        <div className="muted">加载中…</div>
      ) : !list.data || list.data.length === 0 ? (
        <EmptyState
          title="还没有模型配置"
          hint="点击右上角「新建」创建第一条。"
        />
      ) : (
        <div className="kv-list">
          {list.data.map((m) => (
            <div
              key={m.config_id}
              className="kv-list__row"
              data-testid={`model-config-row-${m.config_id}`}
            >
              <span className="kv-list__title">
                {m.capability} / {m.provider} / {m.model}
              </span>
              <span
                className={`badge ${m.enabled === 1 ? 'badge--chapter-committed' : 'badge--archived'}`}
              >
                {m.enabled === 1 ? 'enabled' : 'disabled'}
              </span>
              <span className="kv-list__meta">{m.config_id}</span>
              <span style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
                <button
                  className="btn btn--sm"
                  onClick={async () => {
                    // Sprint 5 review F5：编辑时先 GET 详情拿最新 params_json，
                    // 避免编辑后误以为有值而漏填。
                    // P1-1：读路径 api_key 已被后端脱敏（"***"），前端只用
                    // has_api_key 字段判断「已配置」状态，不回填明文到输入框。
                    try {
                      const detail = await modelConfigsApi.get(m.config_id);
                      setEditing(detail);
                    } catch (e: unknown) {
                      const msg =
                        e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                      setTestResult({
                        config_id: m.config_id,
                        ok: false,
                        latency_ms: 0,
                        detail: `加载详情失败 · ${msg}`,
                        status_code: null,
                      });
                    }
                  }}
                  data-testid={`model-config-edit-${m.config_id}`}
                >
                  编辑
                </button>
                <button
                  className="btn btn--sm"
                  onClick={async () => {
                    setTestResult(null);
                    try {
                      const r = await modelConfigsApi.test(m.config_id);
                      setTestResult({
                        config_id: m.config_id,
                        ok: r.ok,
                        latency_ms: r.latency_ms,
                        detail: r.detail ?? '',
                        status_code: r.status_code ?? null,
                      });
                    } catch (e: unknown) {
                      // 后端 /test 失败时抛 HTTPException：502 等。
                      const msg =
                        e instanceof ApiError ? `${e.status} ${e.detail}` : String(e);
                      setTestResult({
                        config_id: m.config_id,
                        ok: false,
                        latency_ms: 0,
                        detail: `FAIL · ${msg}`,
                        status_code: null,
                      });
                    }
                    void list.reload();
                  }}
                  data-testid={`model-config-test-${m.config_id}`}
                >
                  测试连接
                </button>
                <button
                  className="btn btn--sm btn--danger"
                  onClick={async () => {
                    if (!window.confirm(`确认删除模型配置 ${m.config_id}？`)) return;
                    await modelConfigsApi.delete(m.config_id);
                    void list.reload();
                  }}
                >
                  删除
                </button>
              </span>
            </div>
          ))}
        </div>
      )}

      {testResult ? (
        <InfoBanner>
          <code data-testid="model-config-test-result">
            {testResult.config_id}：
            <span
              className={
                testResult.ok ? 'badge badge--chapter-committed' : 'badge badge--chapter-failed'
              }
              style={{ marginRight: 6 }}
            >
              {testResult.ok ? '成功' : '失败'}
            </span>
            {testResult.latency_ms}ms
            {testResult.status_code != null ? ` · HTTP ${testResult.status_code}` : ''}
            {testResult.detail ? ` · ${testResult.detail}` : ''}
          </code>
        </InfoBanner>
      ) : null}

      {creating ? (
        <ModelConfigFormModal
          title="新建模型配置"
          onCancel={() => setCreating(false)}
          onSubmit={async (payload: ModelConfigCreatePayload) => {
            await modelConfigsApi.create(payload);
            setCreating(false);
            list.reload();
          }}
        />
      ) : null}

      {editing ? (
        <ModelConfigFormModal
          title={`编辑：${editing.config_id}`}
          initial={editing}
          onCancel={() => setEditing(null)}
          onSubmit={async (payload: ModelConfigUpdatePayload) => {
            await modelConfigsApi.update(editing.config_id, payload);
            setEditing(null);
            list.reload();
          }}
        />
      ) : null}
    </div>
  );
}

// =============================================================================
// AgentsPanel
// =============================================================================

function AgentsPanel() {
  const list = useApiCall<Agent[]>(() => agentsApi.list(), []);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncPromptsResult | null>(null);
  const [syncErr, setSyncErr] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const selected = useMemo(
    () => list.data?.find((a) => a.name === selectedName) ?? null,
    [list.data, selectedName],
  );
  const prompts = useApiCall<PromptVersion[]>(
    async () => (selectedName ? agentsApi.listPrompts(selectedName) : Promise.resolve([])),
    [selectedName],
  );

  useEffect(() => {
    if (!selectedName && list.data && list.data.length > 0) {
      setSelectedName(list.data[0].name);
    }
  }, [list.data, selectedName]);

  return (
    <div className="panel" data-testid="agents-panel">
      <div className="panel__title">
        Agents & Prompts
        <div style={{ flex: 1 }} />
        <button
          className="btn btn--sm btn--primary"
          disabled={syncing}
          data-testid="sync-prompts"
          onClick={async () => {
            setSyncErr(null);
            setSyncResult(null);
            setSyncing(true);
            try {
              const r = await agentsApi.sync();
              setSyncResult(r);
              list.reload();
              if (selectedName) prompts.reload();
            } catch (e: unknown) {
              setSyncErr(e instanceof Error ? e.message : '同步失败');
            } finally {
              setSyncing(false);
            }
          }}
        >
          {syncing ? '同步中…' : '同步 prompts'}
        </button>
      </div>

      <ErrorBanner>{list.error}</ErrorBanner>
      <ErrorBanner>{syncErr}</ErrorBanner>

      {syncResult ? (
        <InfoBanner>
          同步完成：扫描 {syncResult.scanned.length}、注册{' '}
          {syncResult.registered.length}、内容更新 {syncResult.updated.length}。
        </InfoBanner>
      ) : null}

      <div className="panel__section">
        <div className="panel__section-title">已注册 agents</div>
        {list.loading ? (
          <div className="muted">加载中…</div>
        ) : !list.data || list.data.length === 0 ? (
          <EmptyState
            title="还没有 agent"
            hint="点击右上角「同步 prompts」从 docs/agents/prompts 扫描。"
          />
        ) : (
          <div className="kv-list">
            {list.data.map((a) => (
              <div
                key={a.agent_id}
                className={`kv-list__row ${a.name === selectedName ? 'kv-list__row--active' : ''}`}
                onClick={() => setSelectedName(a.name)}
                data-testid={`agent-row-${a.name}`}
              >
                <span className="kv-list__title">{a.name}</span>
                <span className="muted small">{a.role}</span>
                <span className="kv-list__meta">
                  {formatDateTime(a.updated_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel__section">
        <div className="panel__section-title">
          {selected ? `${selected.name} 的 prompts` : '选择一个 agent 查看 prompts'}
        </div>
        {selected ? (
          prompts.loading ? (
            <div className="muted">加载中…</div>
          ) : !prompts.data || prompts.data.length === 0 ? (
            <div className="muted small">该 agent 没有 prompt；尝试同步 prompts。</div>
          ) : (
            <div>
              <div className="muted small" style={{ marginBottom: 6 }}>
                共 {prompts.data.length} 个版本（按 version 数字降序）
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>版本</th>
                    <th>状态</th>
                    <th>更新时间</th>
                    <th>内容预览</th>
                  </tr>
                </thead>
                <tbody>
                  {prompts.data.map((p) => (
                    <tr key={p.prompt_id} data-testid={`prompt-row-${p.version}`}>
                      <td>{p.version}</td>
                      <td>{p.status}</td>
                      <td className="muted small">{formatDateTime(p.updated_at)}</td>
                      <td>
                        <code className="small">
                          {p.content.slice(0, 80)}
                          {p.content.length > 80 ? '…' : ''}
                        </code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          <div className="muted small">从左侧列表选择一个 agent。</div>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// ModelConfigFormModal
// =============================================================================

interface ModelConfigFormModalProps {
  title: string;
  initial?: ModelConfig;
  onCancel: () => void;
  /** 编辑/新建由父组件传不同的 submit（create 必填 capability，update 部分更新） */
  onSubmit:
    | ((payload: ModelConfigCreatePayload) => Promise<void>)
    | ((payload: ModelConfigUpdatePayload) => Promise<void>);
}

interface ParamsState {
  baseUrl: string;
  apiKey: string;
}

function ModelConfigFormModal({
  title,
  initial,
  onCancel,
  onSubmit,
}: ModelConfigFormModalProps) {
  const initialParams = (initial?.params_json as Record<string, unknown>) ?? {};
  const [capability, setCapability] = useState<string>(
    initial?.capability ?? CAPABILITY_OPTIONS[0].value,
  );
  const [provider, setProvider] = useState<string>(
    initial?.provider ?? PROVIDER_OPTIONS[0].value,
  );
  const [model, setModel] = useState<string>(initial?.model ?? '');
  // P1-1：编辑模式下不再回填 api_key 明文到输入框（后端读路径已脱敏）。
  // 始终以空串进入；placeholder 与提示文案由 has_api_key 决定。
  const [params, setParams] = useState<ParamsState>({
    baseUrl:
      typeof initialParams['base_url'] === 'string'
        ? String(initialParams['base_url'])
        : '',
    apiKey: '',
  });
  const [enabled, setEnabled] = useState<boolean>(initial ? initial.enabled === 1 : true);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isMock = provider === 'mock';
  const isAnthropic = provider === 'anthropic';
  const isOllama = provider === 'ollama';
  // base_url 必填：仅 mock / anthropic / ollama 可省（Provider 自带默认值），
  // 其余 provider（openai_compatible 等）仍走 Sprint 3 强制必填口径。
  const requireBaseUrl = !isMock && !isAnthropic && !isOllama;
  const baseUrlPlaceholder = isAnthropic
    ? '留空 = 官方 api.anthropic.com'
    : isOllama
      ? '留空 = 本地 http://127.0.0.1:11434'
      : 'https://api.openai.com/v1';

  // P1-1：编辑且后端标记 has_api_key=true 时显示「已配置」，否则按 provider 性质区分。
  const hasApiKey = !!initial?.has_api_key;
  const apiKeyPlaceholder = isAnthropic
    ? hasApiKey
      ? '已配置（留空则不修改）'
      : '（可选；留空则由 resolve_api_key 从 NOVELOS_API_KEY_ANTHROPIC 解析）'
    : hasApiKey
      ? '已配置（留空则不修改）'
      : '（可选；若不填，运行时由 resolve_api_key 从 env 解析）';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!model.trim()) {
      setErr('model 不能为空');
      return;
    }
    if (requireBaseUrl && !params.baseUrl.trim()) {
      setErr('该 provider 需要 base_url');
      return;
    }
    const params_json: Record<string, unknown> = {};
    if (!isMock) {
      const trimmedUrl = params.baseUrl.trim();
      if (trimmedUrl) {
        params_json['base_url'] = trimmedUrl;
      }
      // Ollama 本地不需要 api_key：即便用户填了也丢弃，不写入 params_json。
      // P1-1：编辑 + has_api_key=true 时，若用户留空 → 不传 api_key 字段
      // （后端 _prepare_patch_params 会保留 DB 原值）；若用户填了新值 → 传明文。
      // 新建场景下用户留空同样不传 key。
      if (!isOllama && params.apiKey.trim() !== '') {
        params_json['api_key'] = params.apiKey.trim();
      }
    }
    setSubmitting(true);
    try {
      await onSubmit({
        capability,
        provider,
        model: model.trim(),
        params_json,
        enabled: enabled ? 1 : 0,
      });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  // tryParseJsonObject 用于诊断：UI 当前把 base_url / api_key 用结构化输入；不直接走 JSON textarea。
  // 保留引用以避免 lint 报 unused import（也方便将来扩展）。
  void tryParseJsonObject;
  void formatJson;

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={modalBackdrop}
      onClick={onCancel}
    >
      <form
        className="card"
        style={{ width: 520, maxWidth: '92vw' }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <div className="section-title">{title}</div>
        <ErrorBanner>{err}</ErrorBanner>

        <div className="form-grid">
          <div className="form-row">
            <label>capability *</label>
            <select
              value={capability}
              onChange={(e) => setCapability(e.target.value)}
              data-testid="cfg-capability"
            >
              {CAPABILITY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>provider *</label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              data-testid="cfg-provider"
            >
              {PROVIDER_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-row">
          <label>model *</label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="如 gpt-4o-mini / mock-echo / deepseek-chat"
            data-testid="cfg-model"
          />
        </div>

        {!isMock ? (
          <div className="form-grid">
            <div className="form-row">
              <label>base_url {requireBaseUrl ? '*' : ''}</label>
              <input
                value={params.baseUrl}
                onChange={(e) =>
                  setParams((p) => ({ ...p, baseUrl: e.target.value }))
                }
                placeholder={baseUrlPlaceholder}
                data-testid="cfg-base-url"
              />
            </div>
            {isOllama ? (
              <InfoBanner>Ollama 本地服务无需 API Key —— 表单不接收 key。</InfoBanner>
            ) : (
              <div className="form-row">
                <label>api_key</label>
                <input
                  type="password"
                  value={params.apiKey}
                  onChange={(e) =>
                    setParams((p) => ({ ...p, apiKey: e.target.value }))
                  }
                  placeholder={apiKeyPlaceholder}
                  data-testid="cfg-api-key"
                />
                {initial ? (
                  <div className="muted small" data-testid="cfg-api-key-hint">
                    {hasApiKey
                      ? '已配置密钥。留空保存将保留原值；填入新值则覆盖。'
                      : '当前未配置密钥。留空保存保持未配置。'}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        ) : (
          <InfoBanner>Mock provider 不需要 base_url / api_key。</InfoBanner>
        )}

        <div className="form-row">
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />{' '}
            启用（enabled=1 时可被路由命中）
          </label>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={submitting}
            data-testid="cfg-save"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
        </div>
      </form>
    </div>
  );
}

const modalBackdrop: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15,20,35,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 100,
};
